import { Link } from "react-router-dom";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ApiError } from "@/api/client";

export interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
  /** Optional override for the "not found" back-link target. Defaults to going back in history. */
  backTo?: string;
}

/**
 * Branches on ApiError.status: 401 -> session expired + link to /login;
 * 403 -> no permission; 404 -> not found + back link; 5xx/network ->
 * generic retry button wired to refetch().
 */
export function ErrorState({ error, onRetry, backTo }: ErrorStateProps) {
  const status = error instanceof ApiError ? error.status : null;
  const detail = error instanceof ApiError ? error.detail : "An unexpected error occurred.";

  if (status === 401) {
    return (
      <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Session expired</AlertTitle>
        <AlertDescription>
          Your session has expired.{" "}
          <Link to="/login" className="underline underline-offset-2">
            Log in again
          </Link>
          .
        </AlertDescription>
      </Alert>
    );
  }

  if (status === 403) {
    return (
      <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Access denied</AlertTitle>
        <AlertDescription>You don&apos;t have permission to view this.</AlertDescription>
      </Alert>
    );
  }

  if (status === 404) {
    return (
      <Alert variant="destructive">
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Not found</AlertTitle>
        <AlertDescription className="space-y-2">
          <p>{detail}</p>
          {backTo ? (
            <Link to={backTo} className="underline underline-offset-2">
              Go back
            </Link>
          ) : (
            <button type="button" onClick={() => window.history.back()} className="underline underline-offset-2">
              Go back
            </button>
          )}
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <Alert variant="destructive">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>Something went wrong</AlertTitle>
      <AlertDescription className="space-y-2">
        <p>{detail}</p>
        {onRetry ? (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}
