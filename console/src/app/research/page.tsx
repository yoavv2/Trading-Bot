import { redirect } from "next/navigation";
import { HOME_HREF } from "@/lib/navigation";

export default function ResearchHome() {
  redirect(HOME_HREF);
}
