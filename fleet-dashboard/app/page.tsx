import { redirect } from "next/navigation";

export default function Home() {
  // The admin app starts at the login screen.
  redirect("/login");
}
