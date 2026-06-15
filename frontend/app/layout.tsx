import "./globals.css";
import type { ReactNode } from "react";
import { DM_Sans } from "next/font/google";

const dmSans = DM_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-dm",
});

export const metadata = {
  title: "Personalization Engine",
  description: "Syllabus parsing, RAG, and optimized study scheduling",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={dmSans.variable}>
      <body className={dmSans.className}>{children}</body>
    </html>
  );
}
