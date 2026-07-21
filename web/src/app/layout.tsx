import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Exhibit A — Evidence Engine",
  description: "Code review that may only speak with proof: a runnable failing test, or silence.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body>{children}</body>
    </html>
  );
}
