import { redirect } from "next/navigation";

export default function DashboardIndex() {
  // The dashboard home is the Organizations screen for now.
  redirect("/dashboard/organizations");
}
