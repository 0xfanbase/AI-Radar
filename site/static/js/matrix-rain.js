/*
 * Matrix-theme digital rain -- canvas, progressive enhancement only.
 *
 * This is the ONE deliberate, narrow exception to AI Frontier Wire's
 * otherwise zero-JavaScript architecture (see CLAUDE.md and every other
 * stylesheet's own header comment) -- see IMPROVEMENT_BACKLOG.md for the
 * full account of why this exception was made and what it replaces (the
 * <noscript>-wrapped static CSS/SVG rain layer already in base.html, which
 * is the real fallback whenever this script doesn't run).
 *
 * The algorithm below is a faithful port of a reference implementation the
 * project owner pointed to (a sibling site's own "Digital Rain" effect),
 * ported because a first attempt at an independent implementation -- one
 * glyph per column, every column advancing every single frame -- read as
 * "too fast" and too uniform. The reference's actual technique is
 * DELIBERATELY stepped rather than continuous:
 *   - each column advances only once every 1, 2, or 3 frames (a random
 *     per-column "speed tier", reassigned on every respawn) -- most
 *     columns are NOT moving on any given frame, which is what produces
 *     both the slower felt pace and the visual variety of columns falling
 *     at different speeds;
 *   - not every column is even active at a given moment (dormant columns
 *     occasionally reactivate); and
 *   - already-settled trail glyphs occasionally flicker to a different
 *     character in place, rather than the trail being frozen text sliding
 *     down.
 * Two adaptations from the reference, both logged in IMPROVEMENT_BACKLOG.md:
 *   1. The reference's "surge" (a temporary density/brightness ramp on a
 *      live data event specific to that site) has no equivalent trigger
 *      here -- this is a static site with no live client-side event
 *      stream -- so it is simply omitted, not stubbed out.
 *   2. The reference hardcodes pure white (hex FFFFFF, no leading
 *      punctuation here deliberately -- this file's own regression test
 *      scans for that exact hex-literal shape) for its brightest "hot"
 *      tier. Every color here is instead read from tokens.css at
 *      runtime (never a second hardcoded hex/rgba copy, the same rule
 *      every stylesheet on this site already follows) -- the mid-bright
 *      "neck" tone is produced via a runtime color-mix() of two existing
 *      tokens (matching this project's own established precedent for an
 *      in-between tone -- see board.html's pulse-dot glow) rather than
 *      defining a new token, and the brightest tier reuses the existing
 *      --color-star-white token rather than a hardcoded white.
 *
 * Progressive enhancement, for real: if this script fails to load or run
 * for any reason (JavaScript disabled, blocked, unsupported), the
 * <noscript>-wrapped static CSS/SVG rain layer is the actual fallback --
 * nothing about this site's real content or navigation ever depends on
 * this file executing.
 *
 * Respects prefers-reduced-motion, including a live OS-level toggle while
 * the page is open: no animation loop runs at all when reduced motion is
 * preferred (a single static frame is drawn instead), matching this site's
 * existing reduced-motion convention (the Board pulse-dot, the CSS rain
 * fallback) everywhere else. Also pauses while the tab is hidden, so it
 * never spends battery/CPU drawing to a page nobody can see.
 */
(function () {
  "use strict";

  var canvas = document.getElementById("matrix-rain-canvas");
  if (!canvas || !canvas.getContext) {
    return;
  }
  var ctx = canvas.getContext("2d");
  if (!ctx) {
    return;
  }

  // Digits + half-width katakana (U+FF66-U+FF9D) -- the exact glyph pool
  // the reference implementation uses (matching site/lib/matrix_rain.py's
  // katakana range for the <noscript> fallback, minus that module's extra
  // Latin letters -- dropped here to match the reference precisely).
  var DIGITS = "0123456789";
  var KATAKANA = "";
  for (var cp = 0xff66; cp <= 0xff9d; cp++) {
    KATAKANA += String.fromCharCode(cp);
  }
  var GLYPHS = DIGITS + KATAKANA;

  function readColor(customPropertyName) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(customPropertyName)
      .trim();
  }

  var FONT_SIZE_PX = 16;
  var COLUMN_WIDTH_PX = 20;
  var FRAME_INTERVAL_MS = 50; // 20fps cap -- deliberate, matches the reference
  var ACTIVE_FRACTION = 0.9;
  var REACTIVATE_CHANCE = 0.02; // per-frame odds a dormant column restarts
  var MUTATE_CHANCE = 0.015; // per-frame odds a settled trail glyph flickers
  var HOT_COLUMN_CHANCE = 1 / 40; // odds a respawning column runs "hot" briefly
  var HOT_GLYPH_COUNT = 3;
  var HISTORY_LENGTH = 10; // trail cells eligible for in-place mutation
  var RESPAWN_CHANCE = 0.02; // per-frame odds a past-bottom column resets

  var columns = [];
  var intervalId = null;

  function randomGlyph() {
    return GLYPHS.charAt((Math.random() * GLYPHS.length) | 0);
  }

  function pickSpeedTier() {
    // Weighted roughly 50/35/15 -- about half of all columns step every
    // tick, a little over a third every other tick, the rest every third
    // tick. This variable STEPPING (not a variable per-frame distance) is
    // what produces the reference's slower, less uniform felt pace.
    var r = Math.random();
    if (r < 0.5) return 1;
    if (r < 0.85) return 2;
    return 3;
  }

  function makeColumn() {
    return {
      active: Math.random() < ACTIVE_FRACTION,
      y: Math.random() * -400,
      speedTier: pickSpeedTier(),
      tickCounter: 0,
      history: [], // {y, glyph}, oldest first, capped at HISTORY_LENGTH
      hotRemaining: 0,
    };
  }

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    var columnCount = Math.ceil(canvas.width / COLUMN_WIDTH_PX);
    var next = [];
    for (var i = 0; i < columnCount; i++) {
      next.push(columns[i] || makeColumn());
    }
    columns = next;
    ctx.fillStyle = readColor("--color-bg");
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }

  function stepColumn(column, colors) {
    // Hot-head gradient: the entry from two steps ago finalizes to the
    // trail color, one step ago moves to the mid-bright "neck" color, then
    // the new glyph is drawn at the brightest tier. Older entries beyond
    // this two-step window just keep fading passively via the whole-canvas
    // translucent overlay drawFrame() paints every tick.
    var historyLength = column.history.length;
    if (historyLength >= 1) {
      var previous = column.history[historyLength - 1];
      ctx.fillStyle = column.hotRemaining > 0 ? colors.head : colors.head2;
      ctx.fillText(previous.glyph, column.x, previous.y);
    }
    if (historyLength >= 2) {
      var prior = column.history[historyLength - 2];
      ctx.fillStyle = colors.trail;
      ctx.fillText(prior.glyph, column.x, prior.y);
    }

    // The newest glyph always renders at the brightest tier; during a
    // "hot" run (see hotRemaining above) the neck position one line above
    // it is ALSO promoted to this same brightest tier instead of the
    // dimmer blended "neck" tone -- that promotion is what actually reads
    // as a brief brighter flash, so this line itself needs no ternary.
    var glyph = randomGlyph();
    ctx.fillStyle = colors.head;
    ctx.fillText(glyph, column.x, column.y);
    column.history.push({ y: column.y, glyph: glyph });
    if (column.history.length > HISTORY_LENGTH) {
      column.history.shift();
    }
    if (column.hotRemaining > 0) {
      column.hotRemaining -= 1;
    }

    column.y += FONT_SIZE_PX;
  }

  function drawFrame() {
    var fade = readColor("--color-rain-fade");
    var headHex = readColor("--color-star-white");
    var trailHex = readColor("--color-signal-green");
    var colors = {
      head: headHex,
      trail: trailHex,
      // Mid-bright "neck" tone: a runtime blend of the trail and head
      // tokens, never a third hardcoded/duplicated color -- matches this
      // project's own color-mix() precedent (board.html's pulse-dot glow).
      head2: "color-mix(in srgb, " + trailHex + ", " + headHex + ")",
    };

    ctx.fillStyle = fade;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.font =
      FONT_SIZE_PX +
      'px "MS Gothic", "Osaka-Mono", "Noto Sans Mono CJK JP", monospace';

    for (var i = 0; i < columns.length; i++) {
      var column = columns[i];
      column.x = i * COLUMN_WIDTH_PX;

      if (!column.active) {
        if (Math.random() < REACTIVATE_CHANCE) {
          column.active = true;
          column.y = Math.random() * -100;
          column.speedTier = pickSpeedTier();
          column.history = [];
          column.hotRemaining = Math.random() < HOT_COLUMN_CHANCE ? HOT_GLYPH_COUNT : 0;
        }
        continue;
      }

      // Mutation pass: already-settled trail glyphs (outside the 2-entry
      // hot window still finalizing) occasionally flicker to a new
      // character in place, at their already-decaying brightness -- the
      // reference's trails shimmer, they don't just fall as frozen text.
      var settledCount = Math.max(column.history.length - 2, 0);
      for (var h = 0; h < settledCount; h++) {
        if (Math.random() < MUTATE_CHANCE) {
          column.history[h].glyph = randomGlyph();
          ctx.fillStyle = colors.trail;
          ctx.fillText(column.history[h].glyph, column.x, column.history[h].y);
        }
      }

      column.tickCounter += 1;
      if (column.tickCounter < column.speedTier) {
        continue;
      }
      column.tickCounter = 0;

      stepColumn(column, colors);

      if (column.y > canvas.height + FONT_SIZE_PX && Math.random() < RESPAWN_CHANCE) {
        column.y = Math.random() * -100;
        column.history = [];
        column.speedTier = pickSpeedTier();
        column.active = Math.random() < ACTIVE_FRACTION;
        column.hotRemaining = Math.random() < HOT_COLUMN_CHANCE ? HOT_GLYPH_COUNT : 0;
      }
    }
  }

  function stopAnimating() {
    if (intervalId !== null) {
      clearInterval(intervalId);
      intervalId = null;
    }
  }

  function startAnimating() {
    stopAnimating();
    intervalId = setInterval(function () {
      if (document.hidden) {
        return;
      }
      drawFrame();
    }, FRAME_INTERVAL_MS);
  }

  function applyMotionPreference(prefersReducedMotion) {
    if (prefersReducedMotion) {
      stopAnimating();
      drawFrame();
    } else {
      startAnimating();
    }
  }

  resize();
  window.addEventListener("resize", resize);

  var motionQuery = window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : null;
  applyMotionPreference(motionQuery ? motionQuery.matches : false);
  if (motionQuery && motionQuery.addEventListener) {
    motionQuery.addEventListener("change", function (event) {
      applyMotionPreference(event.matches);
    });
  }
})();
