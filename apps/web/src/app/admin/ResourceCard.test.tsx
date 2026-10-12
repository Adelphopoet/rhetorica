import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ResourceCard } from "./ResourceCard";

it("opens read-only details, restores focus and keeps edit a separate action", async () => {
  const user = userEvent.setup();
  const onEdit = vi.fn();
  const onDelete = vi.fn();
  render(<ResourceCard title="Аналитик" description="Полное описание профиля" icon={<span />} status="активен" onEdit={onEdit} onDelete={onDelete}><p>Подключения: warehouse</p></ResourceCard>);
  expect(screen.queryByText("Подключения: warehouse")).not.toBeInTheDocument();
  const trigger = screen.getByRole("button", { name: "Просмотр: Аналитик" });
  await user.click(trigger);
  await waitFor(() => expect(within(screen.getByRole("dialog")).getByText("Подключения: warehouse")).toBeVisible());
  expect(onEdit).not.toHaveBeenCalled();
  expect(onDelete).not.toHaveBeenCalled();
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(trigger).toHaveFocus();
  await user.click(screen.getByRole("button", { name: "Редактировать" }));
  expect(onEdit).toHaveBeenCalledOnce();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
