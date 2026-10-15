import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "BenefitLens | 내 조건으로 찾는 금전 지원",
  description: "국가, 기업, 재단과 학교의 현금성 지원, 장학금, 등록금과 생활비를 찾는 데이터 프로토타입",
  other: {
    "codex-preview": "development",
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body className="antialiased">{children}</body>
    </html>
  );
}
