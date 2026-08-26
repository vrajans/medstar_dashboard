import { redirect } from "next/navigation";

export default function Home() {
  // Entry point → send users to the dashboard (auth guard lives in the app layout).
  redirect("/overview");
}
