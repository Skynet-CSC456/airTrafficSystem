import assert from "node:assert";
import { testing } from "../../src/command/availability";

//REQ-COM-002 check
describe("REQ-COM-002 - Command availability check", () => {
    it("testing function is available", () => {
        assert.strictEqual(typeof testing, "function");
    });
});

//REQ-COM-AA1-availability check
describe("REQ-COM-AA1-availability - Command availability check", () => {
    it("testing function is available", () => {
        assert.strictEqual(typeof testing, "function");
    });
});
