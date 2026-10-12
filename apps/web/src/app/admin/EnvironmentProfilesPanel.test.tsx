import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EnvironmentProfilesPanel } from "./EnvironmentProfilesPanel";
import type { CodingEnvironmentProfile } from "./types";

const profile: CodingEnvironmentProfile = { id: "coding-local", revision: 8, image_digest: "runner@sha256:abc", software_manifest_digest: "manifest", runtime_class_name: "local_docker", tool_versions: { "dbt-core": "1.11.15" }, max_runtime_seconds: 1200, cpu_limit: "1", memory_limit: "2Gi", disk_limit_bytes: 5 * 1024 ** 3, pid_limit: 128, output_limit_bytes: 64 * 1024 ** 2, dependency_sources: ["https://pypi.org/simple"], pip_dependencies: ["pytest==8.3.5"], created_at: "2026-10-02" };
afterEach(() => vi.unstubAllGlobals());
it("retains disabled profile ID and saves Python packages without client runtime identity", async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json({ ...profile, revision: 9, pip_dependencies: ["pytest==8.3.5", "six==1.17.0"] })); vi.stubGlobal("fetch", fetch);
  render(<EnvironmentProfilesPanel initialProfiles={[profile]} />);
  fireEvent.click(screen.getByRole("button", { name: /coding-local/ }));
  fireEvent.change(screen.getByLabelText("Зависимости Python (pip)"), { target: { value: "pytest==8.3.5\nsix==1.17.0" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить новую версию" }));
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  expect(fetch.mock.calls[0][0]).toBe("/api/admin/coding-environments/coding-local");
  const body = JSON.parse(fetch.mock.calls[0][1].body);
  expect(body).toMatchObject({ id: "coding-local", expected_revision: 8, pip_dependencies: ["pytest==8.3.5", "six==1.17.0"] });
  expect(body.image_digest).toBeUndefined();
  expect(body.tool_versions).toBeUndefined();
  expect(body.runtime_class_name).toBeUndefined();
  await waitFor(() => expect(screen.queryByRole("button", { name: "Сохранить новую версию" })).not.toBeInTheDocument());
});
