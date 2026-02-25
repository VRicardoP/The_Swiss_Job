import { describe, it, expect } from "vitest";
import App from "./App";

describe("App", () => {
  it("should be a function component", () => {
    expect(typeof App).toBe("function");
  });
});
