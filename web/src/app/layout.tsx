import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Exhibit A — Evidence Engine",
  description: "Code review that may only speak with proof: a runnable failing test, or silence.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // Handed to same-origin clients so the UI can reach the guarded routes. This makes the
  // API exactly as private as this page; protect the page itself for real deployments.
  const token = process.env.EXHIBIT_A_API_TOKEN;
  return (
    <html lang="en" className="dark">
      <head>{token ? <meta name="exhibit-a-api-token" content={token} /> : null}</head>
      <body>{children}</body>
    </html>
  );
}
