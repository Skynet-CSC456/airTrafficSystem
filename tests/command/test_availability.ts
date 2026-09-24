import assert from "node:assert";
import { testing } from "../../src/command/availability";

describe("REQ-COM-002 - Command availability check", () => {
    it("testing function is available", () => {
        assert.strictEqual(typeof testing, "function");
    });
});
