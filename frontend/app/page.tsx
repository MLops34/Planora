"use client";

import { useMemo, useState } from "react";
import {
  chatAdjust,
  ChatMessage,
  generateSchedule,
  parseSyllabus,
  ragExtractSchedule,
  ParsedSyllabus,
  ParseQuality,
  ScheduleAnalysis,
  ScheduleBlock,
  Topic,
} from "../lib/api";

const weekdayMap: Record<string, number> = {
  Monday: 0,
  Tuesday: 1,
  Wednesday: 2,
  Thursday: 3,
  Friday: 4,
  Saturday: 5,
  Sunday: 6,
};

const WEEKDAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function formatScheduleDay(isoDate: string) {
  const d = new Date(`${isoDate}T12:00:00`);
  const wd = WEEKDAY_SHORT[d.getDay()];
  const label = d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
  return { wd, label };
}

function topicHsl(title: string): string {
  let h = 0;
  for (let i = 0; i < title.length; i++) {
    h = title.charCodeAt(i) + ((h << 5) - h);
  }
  const hue = 168 + (Math.abs(h) % 48);
  return `${hue} 36% 48%`;
}

function toTopicRows(syllabus: ParsedSyllabus): Topic[] {
  return syllabus.topics.map((topic) => ({
    title: topic.title,
    priority: Number(topic.weightage_percent ?? 1),
    target_minutes: Number(topic.estimated_hours ? topic.estimated_hours * 60 : 0),
    difficulty: 1,
    has_deadline: false,
    deadline: new Date(Date.now() + 14 * 24 * 3600 * 1000).toISOString().slice(0, 10),
  }));
}

export default function HomePage() {
  const [files, setFiles] = useState<File[]>([]);
  const [query, setQuery] = useState("");
  const [useLlm, setUseLlm] = useState(true);
  const [optimizerMode, setOptimizerMode] = useState<"cp_sat" | "greedy">("cp_sat");
  const [includeReviews, setIncludeReviews] = useState(true);
  const [strictMode, setStrictMode] = useState(true);
  const [noStudy, setNoStudy] = useState<string[]>([]);
  const [maxMinutesPerDay, setMaxMinutesPerDay] = useState(240);
  const [minBlockMinutes, setMinBlockMinutes] = useState(30);
  const [maxBlockMinutes, setMaxBlockMinutes] = useState(90);
  const [planningHorizonDays, setPlanningHorizonDays] = useState(56);
  const [syllabus, setSyllabus] = useState<ParsedSyllabus | null>(null);
  const [parseQuality, setParseQuality] = useState<ParseQuality | null>(null);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [blocks, setBlocks] = useState<ScheduleBlock[]>([]);
  const [analysis, setAnalysis] = useState<ScheduleAnalysis[]>([]);
  const [ragAnswer, setRagAnswer] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingKind, setLoadingKind] = useState<"parse" | "rag" | null>(null);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    {
      role: "assistant",
      content:
        "Ask changes like: Priority, Deadlines and Duration.",
    },
  ]);
  const [error, setError] = useState<string | null>(null);

  const agenda = useMemo(() => {
    const grouped: Record<string, ScheduleBlock[]> = {};
    for (const block of blocks) {
      if (!grouped[block.date]) grouped[block.date] = [];
      grouped[block.date].push(block);
    }
    for (const key of Object.keys(grouped)) {
      grouped[key].sort((a, b) => a.start_time.localeCompare(b.start_time));
    }
    return Object.entries(grouped).sort((a, b) => a[0].localeCompare(b[0]));
  }, [blocks]);

  const scheduleKpis = useMemo(() => {
    let study = 0;
    let review = 0;
    for (const b of blocks) {
      if (b.type === "review") review += b.duration_minutes;
      else study += b.duration_minutes;
    }
    const days = new Set(blocks.map((b) => b.date)).size;
    return {
      blocks: blocks.length,
      study,
      review,
      days,
      total: study + review,
    };
  }, [blocks]);

  const primaryFile = files[0] ?? null;

  async function onParse() {
    if (!primaryFile) return;
    setLoading(true);
    setLoadingKind("parse");
    setError(null);
    try {
      const res = await parseSyllabus(primaryFile, useLlm, query.trim() || null);
      setSyllabus(res.syllabus);
      setParseQuality(res.parse_quality);
      setTopics(toTopicRows(res.syllabus));
      setBlocks([]);
      setAnalysis([]);
      setRagAnswer(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Parse failed");
    } finally {
      setLoading(false);
      setLoadingKind(null);
    }
  }

  async function onRagExtract() {
    if (!files.length || !query.trim()) return;
    setLoading(true);
    setLoadingKind("rag");
    setError(null);
    try {
      const res = await ragExtractSchedule(files, query.trim());
      setSyllabus(res.syllabus);
      setParseQuality(res.parse_quality);
      setTopics(toTopicRows(res.syllabus));
      setBlocks([]);
      setAnalysis([]);
      setRagAnswer(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "RAG extraction failed");
    } finally {
      setLoading(false);
      setLoadingKind(null);
    }
  }

  async function runGenerate(topicRows: Topic[]) {
    if (!syllabus) return;
    const res = await generateSchedule({
      syllabus,
      topics: topicRows,
      optimizer_mode: optimizerMode,
      include_reviews: includeReviews,
      strict_mode: strictMode,
      query: query.trim() || undefined,
      no_study_weekdays: noStudy.map((d) => weekdayMap[d]),
      max_minutes_per_day: maxMinutesPerDay,
      min_block_minutes: minBlockMinutes,
      max_block_minutes: maxBlockMinutes,
      planning_horizon_days: planningHorizonDays,
    });
    setBlocks(res.blocks);
    setAnalysis(res.analysis);
    setRagAnswer(res.rag_answer);
  }

  async function onGenerate() {
    if (!syllabus) return;
    setLoading(true);
    setError(null);
    try {
      await runGenerate(topics);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Schedule generation failed");
    } finally {
      setLoading(false);
    }
  }

  async function onChatSend() {
    if (!syllabus || !chatInput.trim()) return;
    const userMessage = chatInput.trim();
    setChatInput("");
    setChatLoading(true);
    setError(null);
    setChatMessages((prev) => [...prev, { role: "user", content: userMessage }]);
    try {
      const adjusted = await chatAdjust({
        syllabus,
        topics,
        message: userMessage,
      });
      const updatedTopics = topics.map((topic) => {
        const patch = adjusted.updates.find((item) => item.title === topic.title);
        if (!patch) return topic;
        return {
          ...topic,
          priority: patch.priority ?? topic.priority,
          target_minutes: patch.target_minutes ?? topic.target_minutes,
          difficulty: patch.difficulty ?? topic.difficulty,
          has_deadline: patch.has_deadline ?? topic.has_deadline,
          deadline: patch.deadline ?? topic.deadline,
        };
      });
      setTopics(updatedTopics);
      await runGenerate(updatedTopics);
      setChatMessages((prev) => [...prev, { role: "assistant", content: adjusted.reply }]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Chat adjustment failed";
      setError(msg);
      setChatMessages((prev) => [
        ...prev,
        { role: "assistant", content: `I could not apply that: ${msg}` },
      ]);
    } finally {
      setChatLoading(false);
    }
  }

  function updateTopic(index: number, key: keyof Topic, value: string | number | boolean) {
    setTopics((prev) =>
      prev.map((topic, i) => (i === index ? { ...topic, [key]: value } : topic)),
    );
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header-inner">
          <p className="app-brand">Study intelligence</p>
          <h1>Personalized teaching assistant</h1>
          <p className="subtitle">
            Parse your syllabus, tune priorities, and generate an optimized plan with live chat
            adjustments.
          </p>
        </div>
      </header>

      <main className="container">
        <div className="layout">
          <div className="planner-pane">
      <section className="card">
        <h2>Upload and Parse</h2>
        <div className="grid">
          <div>
            <label>Upload syllabus PDF (multiple for guided RAG)</label>
            <input
              type="file"
              accept=".pdf"
              multiple
              onChange={(e) =>
                setFiles(e.target.files?.length ? Array.from(e.target.files) : [])
              }
            />
            {files.length > 1 && (
              <p className="card-kicker" style={{ marginTop: "0.5rem" }}>
                Parse uses the first file only. Use &quot;Extract with RAG&quot; to combine all
                PDFs using your question below.
              </p>
            )}
          </div>
          <div>
            <label title="Parse (LLM): steers the model toward matching sections. Schedule: RAG answer over the syllabus. Guided RAG extract: retrieval query for multi-PDF extraction (e.g. list required subjects).">
              Retrieval Question(guides parsing)
            </label>
            <input value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
        </div>
        <div className="row">
          <label>
            <input
              type="checkbox"
              checked={useLlm}
              onChange={(e) => setUseLlm(e.target.checked)}
            />
            Use LLM parsing
          </label>
          <button disabled={!primaryFile || loading} onClick={onParse}>
            {loading && loadingKind === "parse" ? "Parsing..." : "Parse Syllabus"}
          </button>
          <button
            type="button"
            disabled={!files.length || !query.trim() || loading}
            onClick={onRagExtract}
            title="Requires a non-empty question. Best for multiple PDFs or table-heavy syllabi."
          >
            {loading && loadingKind === "rag" ? "Extracting..." : "Extract"}
          </button>
        </div>
      </section>

      {parseQuality && (
        <section className="card metrics">
          <div>
            <p>Topics</p>
            <strong>{topics.length}</strong>
          </div>
          <div>
            <p>Raw text lines</p>
            <strong>{parseQuality.lines}</strong>
          </div>
          <div>
            <p>Heading-like lines</p>
            <strong>{parseQuality.heading_like_lines}</strong>
          </div>
          <div>
            <p>Parse quality</p>
            <strong>{parseQuality.score}/100</strong>
          </div>
        </section>
      )}

      {syllabus && (
        <section className="card">
          <h2>Priority Settings</h2>
          <div className="row controls">
            <label>
              Optimizer
              <select
                value={optimizerMode}
                onChange={(e) => setOptimizerMode(e.target.value as "cp_sat" | "greedy")}
              >
                <option value="cp_sat">cp_sat</option>
                <option value="greedy">greedy</option>
              </select>
            </label>
            <label>
              <input
                type="checkbox"
                checked={includeReviews}
                onChange={(e) => setIncludeReviews(e.target.checked)}
              />
              Include reviews
            </label>
            <label>
              <input
                type="checkbox"
                checked={strictMode}
                onChange={(e) => setStrictMode(e.target.checked)}
              />
              Strict mode
            </label>
          </div>

          <label>No-study weekdays</label>
          <div className="weekday-row">
            {Object.keys(weekdayMap).map((day) => (
              <label key={day}>
                <input
                  type="checkbox"
                  checked={noStudy.includes(day)}
                  onChange={(e) =>
                    setNoStudy((prev) =>
                      e.target.checked ? [...prev, day] : prev.filter((d) => d !== day),
                    )
                  }
                />
                {day}
              </label>
            ))}
          </div>

          <p className="card-kicker" style={{ marginTop: "1rem" }}>
            Daily limits and planning window (sent to the optimizer).
          </p>
          <div className="limits-row">
            <label>
              Max minutes / day
              <input
                type="number"
                min={60}
                max={720}
                step={15}
                value={maxMinutesPerDay}
                onChange={(e) => setMaxMinutesPerDay(Number(e.target.value))}
              />
            </label>
            <label>
              Min block (min)
              <input
                type="number"
                min={15}
                max={120}
                step={5}
                value={minBlockMinutes}
                onChange={(e) => setMinBlockMinutes(Number(e.target.value))}
              />
            </label>
            <label>
              Max block (min)
              <input
                type="number"
                min={30}
                max={240}
                step={5}
                value={maxBlockMinutes}
                onChange={(e) => setMaxBlockMinutes(Number(e.target.value))}
              />
            </label>
            <label>
              Horizon (days)
              <input
                type="number"
                min={7}
                max={180}
                step={1}
                value={planningHorizonDays}
                onChange={(e) => setPlanningHorizonDays(Number(e.target.value))}
              />
            </label>
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Topic</th>
                  <th>Priority</th>
                  <th>Target Minutes</th>
                  <th>Difficulty</th>
                  <th>Has Deadline</th>
                  <th>Deadline</th>
                </tr>
              </thead>
              <tbody>
                {topics.map((topic, idx) => (
                  <tr key={topic.title}>
                    <td>{topic.title}</td>
                    <td>
                      <input
                        type="number"
                        step={0.1}
                        min={0.1}
                        max={100}
                        value={topic.priority}
                        onChange={(e) => updateTopic(idx, "priority", Number(e.target.value))}
                      />
                    </td>
                    <td>
                      <input
                        type="number"
                        step={15}
                        min={0}
                        max={5000}
                        value={topic.target_minutes}
                        onChange={(e) =>
                          updateTopic(idx, "target_minutes", Number(e.target.value))
                        }
                      />
                    </td>
                    <td>
                      <input
                        type="number"
                        step={0.1}
                        min={0.5}
                        max={3}
                        value={topic.difficulty}
                        onChange={(e) => updateTopic(idx, "difficulty", Number(e.target.value))}
                      />
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        checked={topic.has_deadline}
                        onChange={(e) => updateTopic(idx, "has_deadline", e.target.checked)}
                      />
                    </td>
                    <td>
                      <input
                        type="date"
                        disabled={!topic.has_deadline}
                        value={topic.deadline}
                        onChange={(e) => updateTopic(idx, "deadline", e.target.value)}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <button disabled={loading} onClick={onGenerate}>
            {loading ? "Generating..." : "Generate Schedule"}
          </button>
        </section>
      )}

      {!!ragAnswer && (
        <section className="card">
          <h2>RAG answer</h2>
          <p className="rag-answer">{ragAnswer}</p>
        </section>
      )}

      {blocks.length > 0 && (
        <section className="card schedule-card">
          <h2>Study schedule</h2>
          <p className="card-kicker">
            Color-coded by topic; reviews use a warm accent. Bar width shows share of that day.
          </p>

          <div className="schedule-kpis">
            <div className="schedule-kpi">
              <span>Scheduled days</span>
              <strong>{scheduleKpis.days}</strong>
            </div>
            <div className="schedule-kpi">
              <span>Total blocks</span>
              <strong>{scheduleKpis.blocks}</strong>
            </div>
            <div className="schedule-kpi">
              <span>Study minutes</span>
              <strong>{scheduleKpis.study}</strong>
            </div>
            <div className="schedule-kpi review">
              <span>Review minutes</span>
              <strong>{scheduleKpis.review}</strong>
            </div>
          </div>

          <div className="schedule-days">
            {agenda.map(([day, dayBlocks]) => {
              const dayTotal = dayBlocks.reduce((acc, b) => acc + b.duration_minutes, 0);
              const { wd, label } = formatScheduleDay(day);
              return (
                <article key={day} className="schedule-day">
                  <header>
                    <span className="schedule-day-wd">{wd}</span>
                    <span className="schedule-day-date">{label}</span>
                    <span className="schedule-day-total">{dayTotal} min planned</span>
                  </header>
                  <div className="schedule-blocks">
                    {dayBlocks.map((block, i) => {
                      const pct = dayTotal > 0 ? (block.duration_minutes / dayTotal) * 100 : 0;
                      const typeClass = block.type === "review" ? "review" : "study";
                      const rail =
                        block.type === "review"
                          ? undefined
                          : { background: `hsl(${topicHsl(block.topic)})` };
                      return (
                        <div
                          key={`${day}-${i}`}
                          className={`schedule-block type-${typeClass}`}
                        >
                          <div className="schedule-block-rail" style={rail} />
                          <div className="schedule-block-time">
                            {block.start_time.slice(0, 5)}
                          </div>
                          <div className="schedule-block-body">
                            <div className="schedule-block-top">
                              <span className="schedule-topic" title={block.topic}>
                                {block.topic}
                              </span>
                              <span className="schedule-chip">{block.type}</span>
                              <span className="schedule-duration">{block.duration_minutes} min</span>
                            </div>
                            <div className="schedule-bar-track">
                              <div
                                className="schedule-bar-fill"
                                style={{ width: `${Math.max(pct, 3)}%` }}
                              />
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      )}

      {analysis.length > 0 && (
        <section className="card">
          <h2>Coverage analysis</h2>
          <p className="card-kicker">Target vs planned minutes and deadline risk.</p>
          <div className="table-wrap">
            <table className="analysis-table">
              <thead>
                <tr>
                  <th>Topic</th>
                  <th>Target</th>
                  <th>Planned</th>
                  <th>Coverage</th>
                  <th>Score</th>
                  <th>Overdue</th>
                </tr>
              </thead>
              <tbody>
                {analysis.map((row) => {
                  const cov = row.coverage_pct;
                  let covClass = "cov-warn";
                  if (cov == null) covClass = "";
                  else if (cov >= 90) covClass = "cov-ok";
                  else if (cov < 60) covClass = "cov-bad";
                  const odClass =
                    row.overdue_minutes > 0 ? "overdue-bad" : "overdue-zero";
                  return (
                    <tr key={row.topic}>
                      <td>{row.topic}</td>
                      <td>{row.target_minutes}</td>
                      <td>{row.planned_minutes}</td>
                      <td className={covClass}>
                        {cov != null ? `${cov}%` : "—"}
                      </td>
                      <td>{row.strict_score}</td>
                      <td className={odClass}>{row.overdue_minutes}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
        </div>

        <aside className="chat-pane">
          <section className="card chat-card">
            <h2>Planner Chat</h2>
            <p className="chat-subtitle">
              Ask for real-time updates to priorities, minutes, and deadlines.
            </p>
            <div className="chat-log">
              {chatMessages.map((msg, idx) => (
                <div
                  key={`${msg.role}-${idx}`}
                  className={`chat-bubble ${msg.role === "user" ? "chat-user" : "chat-assistant"}`}
                >
                  {msg.content}
                </div>
              ))}
              {chatLoading && <div className="chat-bubble chat-assistant">Applying updates...</div>}
            </div>
            <div className="chat-input-row">
              <textarea
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="Increase priority of Unit 2 and set deadline to 2026-06-10"
                rows={3}
              />
              <button disabled={!syllabus || chatLoading || !chatInput.trim()} onClick={onChatSend}>
                {chatLoading ? "Working..." : "Send"}
              </button>
            </div>
          </section>
        </aside>
      </div>

      {error && <p className="error">{error}</p>}
      </main>
    </div>
  );
}
