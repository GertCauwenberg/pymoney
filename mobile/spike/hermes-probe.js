/**
 * E0.0 spike: does the Hermes JS engine support everything `money.ts` and
 * `ledger.ts` need?
 *
 * Features probed (from money.ts / ledger.ts):
 *   - BigInt literals and arithmetic (10n ** n, /, %, unary minus, comparisons)
 *   - BigInt constructor from number and string, toString
 *   - Intl.NumberFormat.formatToParts
 *   - Intl.NumberFormat option signDisplay: "always"
 *   - Intl.NumberFormat locale separator behaviour (group / decimal / minusSign)
 *   - String.padStart / padEnd
 *   - Map with string keys (separatorCache)
 *
 * Run under Node for a baseline, then under Hermes:
 *   node hermes-probe.js
 *   hermes hermes-probe.js
 *
 * Exit code 0 = every check passed. Non-zero = a gap that money.ts must shim.
 */

"use strict";

var failures = [];
var passes = 0;

function check(name, fn) {
  try {
    var result = fn();
    if (result === true) {
      passes += 1;
      print("PASS  " + name);
    } else {
      failures.push({ name: name, detail: String(result) });
      print("FAIL  " + name + "  →  " + result);
    }
  } catch (e) {
    failures.push({ name: name, detail: String(e) });
    print("FAIL  " + name + "  →  threw " + e);
  }
}

// print() exists on Hermes; Node uses console.log.
if (typeof print === "undefined") {
  globalThis.print = function (msg) {
    console.log(msg);
  };
}

// ---- BigInt core (money.ts: minor units are always BigInt) ----------------

check("BigInt literal 10n", function () {
  return 10n === 10n;
});

check("10n ** BigInt(n)  (decimalsOf, formatRatePercent)", function () {
  if (10n ** 2n !== 100n) return "10n ** 2n = " + (10n ** 2n);
  if (10n ** 0n !== 1n) return "10n ** 0n = " + (10n ** 0n);
  if (10n ** 8n !== 100000000n) return "10n ** 8n = " + (10n ** 8n);
  return true;
});

check("BigInt division truncates toward zero  (decimalsOf)", function () {
  if (100n / 10n !== 10n) return "100n/10n";
  if (-7n / 2n !== -3n) return "(-7n)/2n = " + -7n / 2n;
  return true;
});

check("BigInt remainder  (formatRatePercent)", function () {
  if (7n % 3n !== 1n) return "7n%3n";
  if (-7n % 3n !== -1n) return "(-7n)%3n = " + -7n % 3n;
  return true;
});

check("BigInt unary minus and comparisons", function () {
  var v = -12345678901234567890n;
  if (v > 0n) return "negative compared wrong";
  if (-v !== 12345678901234567890n) return "unary minus";
  return true;
});

check("BigInt(number) and BigInt(string)", function () {
  if (BigInt(123) !== 123n) return "BigInt(123)";
  if (BigInt("9007199254740993") !== 9007199254740993n) return "BigInt(string) precision";
  return true;
});

check("BigInt.prototype.toString  (formatMinor)", function () {
  var v = 9007199254740993n;
  if (v.toString() !== "9007199254740993") return v.toString();
  return true;
});

check("2^53 + 1 keeps the last digit (never a float)", function () {
  // The money.test.ts case: formatMinor(9007199254740993n, 100) === "90,071,992,547,409.93"
  var minor = 9007199254740993n;
  var magnitude = minor.toString();
  if (magnitude !== "9007199254740993") return magnitude;
  // The float path would produce 9007199254740992:
  if (Number(minor) === 9007199254740992 && minor !== 9007199254740992n) {
    // fine — we never convert back; the BigInt is intact
  }
  return true;
});

// ---- Intl.NumberFormat (money.ts: separators()) ---------------------------

check("Intl.NumberFormat exists", function () {
  return typeof Intl !== "undefined" && typeof Intl.NumberFormat === "function";
});

check("formatToParts exists", function () {
  var f = new Intl.NumberFormat("en-US");
  return typeof f.formatToParts === "function";
});

check("signDisplay: \"always\" is accepted", function () {
  var f = new Intl.NumberFormat("en-US", { signDisplay: "always" });
  var parts = f.formatToParts(-1234567.5);
  if (!Array.isArray(parts) || parts.length === 0) return "no parts";
  return true;
});

check("formatToParts types: group, decimal, minusSign", function () {
  var parts = new Intl.NumberFormat("en-US", { signDisplay: "always" }).formatToParts(-1234567.5);
  function find(type) {
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].type === type) return parts[i].value;
    }
    return undefined;
  }
  var group = find("group");
  var decimal = find("decimal");
  var minus = find("minusSign");
  if (group !== ",") return "group=" + group;
  if (decimal !== ".") return "decimal=" + decimal;
  if (minus !== "-") return "minusSign=" + minus;
  return true;
});

check("de-DE separators (group=., decimal=,)", function () {
  var parts = new Intl.NumberFormat("de-DE", { signDisplay: "always" }).formatToParts(-1234567.5);
  function find(type) {
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].type === type) return parts[i].value;
    }
    return undefined;
  }
  var group = find("group");
  var decimal = find("decimal");
  if (group !== ".") return "group=" + group;
  if (decimal !== ",") return "decimal=" + decimal;
  return true;
});

check("Intl.NumberFormat without locale (undefined)", function () {
  var parts = new Intl.NumberFormat(undefined, { signDisplay: "always" }).formatToParts(-1234567.5);
  return Array.isArray(parts) && parts.length > 0;
});

// ---- String / Map helpers used by money.ts --------------------------------

check("String.padStart / padEnd  (formatMinor)", function () {
  if ("5".padStart(3, "0") !== "005") return "padStart";
  if ("5".padEnd(3, "0") !== "500") return "padEnd";
  return true;
});

check("Map get/set with string keys  (separatorCache)", function () {
  var m = new Map();
  m.set("en-US", { group: "," });
  return m.get("en-US").group === "," && m.get("de-DE") === undefined;
});

// ---- End-to-end: replicate formatMinor's critical path --------------------

check("formatMinor critical path: 9007199254740993n @ scale 100", function () {
  // Inlined copy of money.ts:formatMinor's arithmetic (no module system here).
  var minor = 9007199254740993n;
  var scale = 100;
  var locale = "en-US";
  var parts = new Intl.NumberFormat(locale, { signDisplay: "always" }).formatToParts(-1234567.5);
  function find(type, fallback) {
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].type === type) return parts[i].value;
    }
    return fallback;
  }
  var group = find("group", ",");
  var decimalSep = find("decimal", ".");
  var minusSep = find("minusSign", "-");
  var negative = minor < 0n;
  var magnitude = (negative ? -minor : minor).toString();
  // decimalsOf(scale)
  var n = 0;
  var s = BigInt(scale);
  while (s > 1n) {
    s /= 10n;
    n += 1;
  }
  var decimals = n;
  var padded = magnitude.padStart(decimals + 1, "0");
  var whole = padded.slice(0, padded.length - decimals);
  var fraction = padded.slice(padded.length - decimals);
  // groupDigits
  var chunks = [];
  for (var i = whole.length; i > 0; i -= 3) {
    chunks.unshift(whole.slice(Math.max(0, i - 3), i));
  }
  var text = chunks.join(group);
  if (decimals > 0) text += decimalSep + fraction;
  if (negative) text = minusSep + text;
  if (text !== "90,071,992,547,409.93") return text;
  return true;
});

check("parseDecimal critical path: \"12.5\" @ scale 100 → 1250n", function () {
  var DECIMAL = /^(-?)(\d*)(?:\.(\d*))?$/;
  var text = "12.5";
  var scale = 100;
  var m = DECIMAL.exec(text.trim().replace(",", "."));
  if (!m) return "no match";
  var sign = m[1];
  var whole = m[2] === undefined ? "" : m[2];
  var fraction = m[3] === undefined ? "" : m[3];
  if (whole === "" && fraction === "") return "empty";
  var decimals = 2; // scale 100
  if (fraction.length > decimals) return "too many decimals";
  var minor = BigInt((whole || "0") + fraction.padEnd(decimals, "0"));
  var result = sign === "-" ? -minor : minor;
  if (result !== 1250n) return String(result);
  return true;
});

check("draftBalance critical path: sum of legs is BigInt", function () {
  var values = ["12.50", "3.25", ""];
  var sum = 0n;
  var blank = false;
  for (var i = 0; i < values.length; i++) {
    if (values[i].trim() === "") {
      blank = true;
      continue;
    }
    var m = /^(-?)(\d*)(?:\.(\d*))?$/.exec(values[i].trim().replace(",", "."));
    var whole = m[2] === undefined ? "" : m[2];
    var fraction = m[3] === undefined ? "" : m[3];
    var minor = BigInt((whole || "0") + fraction.padEnd(2, "0"));
    sum += m[1] === "-" ? -minor : minor;
  }
  if (!blank) return "blank not detected";
  if (sum !== 1575n) return "sum=" + sum;
  return true;
});

// ---- Report --------------------------------------------------------------

print("");
print("Passed: " + passes + "   Failed: " + failures.length);
if (failures.length > 0) {
  print("GAPS:");
  for (var i = 0; i < failures.length; i++) {
    print("  - " + failures[i].name + ": " + failures[i].detail);
  }
  // Non-zero exit where the runtime supports it (Hermes and Node both do).
  if (typeof process !== "undefined" && process.exit) {
    process.exit(1);
  }
} else {
  print("All money.ts / ledger.ts primitives are available on this engine.");
}
