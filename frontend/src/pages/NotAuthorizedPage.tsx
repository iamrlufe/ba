import { Link } from "react-router-dom";
import { ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";

export function NotAuthorizedPage() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
      <ShieldAlert className="h-10 w-10 text-muted-foreground" />
      <h1 className="text-xl font-semibold">Not authorized</h1>
      <p className="max-w-md text-sm text-muted-foreground">
        You don&apos;t have permission to view this page. If you believe this is a mistake, contact an
        administrator.
      </p>
      <Button asChild variant="outline">
        <Link to="/dashboard">Back to dashboard</Link>
      </Button>
    </div>
  );
}
