import type { Metadata } from "next";
import { DM_Mono, Instrument_Serif, Manrope } from "next/font/google";
import "./globals.css";

const sans = Manrope({ variable: "--font-sans", subsets: ["latin"] });
const serif = Instrument_Serif({ variable: "--font-serif", subsets: ["latin"], weight: "400" });
const mono = DM_Mono({ variable: "--font-mono", subsets: ["latin"], weight: ["400", "500"] });

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"),
  title: "Paperclock — Your files know what’s coming",
  description: "A private, local deadline radar for the commitments hiding in your files.",
  openGraph: {
    title: "Paperclock",
    description: "Your files know what’s coming.",
    images: [{ url: "/og.png", width: 1536, height: 1024, alt: "Paperclock deadline radar" }],
  },
  twitter: { card: "summary_large_image", title: "Paperclock", description: "Your files know what’s coming.", images: ["/og.png"] },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body className={`${sans.variable} ${serif.variable} ${mono.variable}`}>{children}</body></html>;
}
