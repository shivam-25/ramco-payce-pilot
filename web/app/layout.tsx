import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Ramco Payce support pilot",
  description:
    "PII gate, classify, dedupe, route, answer with citations, human hand-off.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
