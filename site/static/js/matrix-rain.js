/*
 * Matrix-theme digital rain -- canvas, progressive enhancement only.
 *
 * This is the ONE deliberate, narrow exception to AI Frontier Wire's
 * otherwise zero-JavaScript architecture (see CLAUDE.md and every other
 * stylesheet's own header comment). It exists because a canvas redrawn
 * every frame with true per-glyph randomness and real alpha-blended
 * trailing fade produces the source material's actual look in a way a
 * static, pre-rendered CSS/SVG tile structurally cannot -- see
 * IMPROVEMENT_BACKLOG.md for the full account of why this exception was
 * made and what it replaces.
 *
 * Progressive enhancement, for real: if this script fails to load or run
 * for any reason (JavaScript disabled, blocked, unsupported), the
 * <noscript>-wrapped static CSS/SVG rain layer already in base.html is the
 * actual fallback -- nothing about this site's real content or navigation
 * ever depends on this file executing.
 *
 * Every color is read from the live tokens.css custom properties at
 * runtime (never a second hardcoded hex/rgba copy here) -- the same
 * single-source-of-truth rule every other stylesheet on this site follows.
 *
 * Respects prefers-reduced-motion, including a live OS-level toggle while
 * the page is open: no animation loop runs at all when reduced motion is
 * preferred (a single static frame is drawn instead), matching this site's
 * existing reduced-motion convention (the Board pulse-dot, the CSS rain
 * fallback) everywhere else.
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

  // Half-width katakana (U+FF66-U+FF9D) -- the actual glyph range the
  // source material's on-screen effect draws from -- mixed with digits and
  // capital Latin letters, matching site/lib/matrix_rain.py's own glyph
  // pool (the CSS/SVG fallback this canvas visually replaces when JS runs).
  var GLYPHS = [];
  for (var cp = 0xff66; cp <= 0xff9d; cp++) {
    GLYPHS.push(String.fromCharCode(cp));
  }
  for (var d = 0; d <= 9; d++) {
    GLYPHS.push(String(d));
  }
  for (var a = 65; a <= 90; a++) {
    GLYPHS.push(String.fromCharCode(a));
  }

  function readColor(customPropertyName) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(customPropertyName)
      .trim();
  }

  var FONT_SIZE_PX = 16;
  var COLUMN_WIDTH_PX = FONT_SIZE_PX;
  var HEAD_GLYPH_CHANCE = 0.02;
  var COLUMN_RESET_CHANCE = 0.025;
  var FRAME_INTERVAL_MS = 40;

  var columnCount = 0;
  var dropRows = [];
  var intervalId = null;

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    columnCount = Math.ceil(canvas.width / COLUMN_WIDTH_PX);
    dropRows = [];
    for (var i = 0; i < columnCount; i++) {
      dropRows.push(Math.floor((Math.random() * canvas.height) / FONT_SIZE_PX));
    }
    ctx.fillStyle = readColor("--color-bg");
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }

  function drawFrame() {
    ctx.fillStyle = readColor("--color-rain-fade");
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.font = FONT_SIZE_PX + "px monospace";
    var trailColor = readColor("--color-signal-green");
    var headColor = readColor("--color-star-white");

    for (var i = 0; i < columnCount; i++) {
      var glyph = GLYPHS[(Math.random() * GLYPHS.length) | 0];
      ctx.fillStyle = Math.random() < HEAD_GLYPH_CHANCE ? headColor : trailColor;
      ctx.fillText(glyph, i * COLUMN_WIDTH_PX, dropRows[i] * FONT_SIZE_PX);

      var pastBottom = dropRows[i] * FONT_SIZE_PX > canvas.height;
      if (pastBottom && Math.random() > 1 - COLUMN_RESET_CHANCE) {
        dropRows[i] = 0;
      } else {
        dropRows[i]++;
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
    intervalId = setInterval(drawFrame, FRAME_INTERVAL_MS);
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
