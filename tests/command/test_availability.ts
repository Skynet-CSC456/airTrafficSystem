import assert from "node:assert";
import { testing } from "../../src/command/availability";

// REQ-CMD-002: Command availability check
describe("REQ-CMD-002 - Command availability check", () => {
    it("testing function is available", () => {
        assert.strictEqual(typeof testing, "function");
    });
});

// REQ-CMD-003: Command availability check
describe("REQ-CMD-003 - Command availability check", () => {
    it("testing function is available", () => {
        assert.strictEqual(typeof testing, "function");
    });
});