import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { DropdownSelect, Sheet } from "@rhetorica/ui";
import { selectOption } from "@/test/selectOption";

const options = [
  { value: "", label: "Не выбрано" },
  { value: "default", label: "Default", description: "Владелец" },
  { value: "unavailable", label: "Недоступно", disabled: true },
  { value: "analytics", label: "Analytics", description: "Аналитик" },
];
function Field() {
  const [value, setValue] = useState("default");
  return <><DropdownSelect label="Workspace" value={value} onValueChange={setValue} options={options} showDescription /><output data-testid="selected">{value}</output></>;
}

describe("DropdownSelect", () => {
  it("selects with the keyboard, skips disabled options and returns focus", async () => {
    const user = userEvent.setup();
    render(<Field />);
    const trigger = screen.getByRole("combobox", { name: "Workspace" });
    trigger.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("option", { name: "Default" })).toHaveFocus();
    await user.keyboard("{ArrowDown}{Enter}");
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(trigger).toHaveTextContent("Analytics");
    expect(screen.getByTestId("selected")).toHaveTextContent("analytics");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("allows clearing a selection and cancelling without changing it", async () => {
    const user = userEvent.setup();
    render(<Field />);
    await selectOption("Workspace", "Не выбрано");
    expect(screen.getByTestId("selected")).toBeEmptyDOMElement();
    const trigger = screen.getByRole("combobox", { name: "Workspace" });
    trigger.focus();
    await user.keyboard("{Enter}{End}{Escape}");
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(trigger).toHaveTextContent("Не выбрано");
    expect(screen.getByTestId("selected")).toBeEmptyDOMElement();
  });

  it("keeps the parent sheet open when Escape dismisses its menu", async () => {
    const onOpenChange = vi.fn();
    render(<Sheet open onOpenChange={onOpenChange} title="Настройки"><Field /></Sheet>);
    fireEvent.keyDown(screen.getByRole("combobox", { name: "Workspace" }), { key: "Enter" });
    fireEvent.keyDown(await screen.findByRole("option", { name: "Default" }), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("listbox")).not.toBeInTheDocument());
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("respects a disabled fieldset", async () => {
    const user = userEvent.setup();
    render(<fieldset disabled><Field /></fieldset>);
    const trigger = screen.getByRole("combobox", { name: "Workspace" });
    expect(trigger).toBeDisabled();
    await user.click(trigger);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByTestId("selected")).toHaveTextContent("default");
  });
});
