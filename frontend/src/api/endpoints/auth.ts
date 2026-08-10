import { apiFetch } from "../client";
import type { LoginRequest, LoginResponse, UserRead } from "../types";

export async function login(payload: LoginRequest): Promise<LoginResponse> {
  return apiFetch<LoginResponse>("/auth/login", { method: "POST", body: payload });
}

export async function getMe(token: string): Promise<UserRead> {
  return apiFetch<UserRead>("/auth/me", { token });
}
