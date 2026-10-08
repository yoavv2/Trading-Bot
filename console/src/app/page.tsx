import { redirect } from "next/navigation";
import { HOME_HREF } from "@/lib/navigation";

/** The product opens on the research studies page. `next.config.ts` also redirects `/`. */
export default function Home() {
  redirect(HOME_HREF);
}
