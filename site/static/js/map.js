/*
 * World-map marker interaction -- click/expand popovers, "expand all",
 * the open-weights filter toggle, and (owner follow-on to the Phases
 * 6-9 map-centric UI reshape) pan + zoom on the map canvas itself:
 * mouse-wheel zoom toward the cursor, pinch-to-zoom toward the pinch
 * midpoint, click-and-drag / single-finger-drag panning, and explicit
 * +/-/reset <button> controls, with tap-vs-drag disambiguation so a
 * drag gesture that starts on a marker never also opens its popover.
 *
 * This is the site's SECOND deliberate, narrow exception to an
 * otherwise zero-JavaScript architecture (the first is
 * site/static/js/matrix-rain.js -- see that file's own header comment
 * and IMPROVEMENT_BACKLOG.md for the owner sign-off ritual this repeats).
 * It is only ever included on the map homepage (site/templates/
 * map_index.html), never on any other page -- see
 * site/tests/test_build.py's script-tag allowlist test. No new library,
 * no CDN -- plain CSS `transform: translate(...) scale(...)` on the
 * existing `.map-wrap` element, driven by a small `{scale, x, y}` state
 * object, per the project's no-new-runtime-dependency discipline.
 *
 * Progressive enhancement, for real: every marker's company-NAME text
 * (`.map-marker__name`) is a plain `<a href="/companies/<slug>/">`
 * rendered directly by site/builders/map.py/templates/map_index.html at
 * build time -- reachable and readable with this script never loading,
 * blocked, or failing partway through. This script attaches behavior
 * ONLY to the separate marker GLYPH button (`.map-marker__glyph`), the
 * two toggle buttons above the map, the map viewport itself (pan/zoom
 * gestures), and the three zoom-control buttons; it never rewrites,
 * hides, or gates the name link itself. If this file fails to run for
 * any reason, every popover simply stays collapsed (its `hidden`
 * attribute, set by the server-rendered HTML, is native and requires no
 * script to take effect), the toggle/zoom buttons are inert, and the
 * map renders at its plain CSS default view (see map_index.html's own
 * `.map-wrap` rule) with no pan/zoom possible -- the map is still fully
 * readable and every company page is still one tap away via its name
 * link.
 */
(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState !== "loading") {
      fn();
    } else {
      document.addEventListener("DOMContentLoaded", fn);
    }
  }

  function clamp(value, lo, hi) {
    return Math.min(Math.max(value, lo), hi);
  }

  ready(function () {
    var markers = document.querySelectorAll(".map-marker");
    if (!markers.length) {
      return;
    }

    // Flip the CSS "is this script actually running" flag (see
    // map_index.html's own .map-toggle / .map-zoom-controls /
    // body.map-js-ready rules) -- only reached once we know a script
    // environment exists and this file itself is executing, not merely
    // that the DOM is ready.
    document.body.classList.add("map-js-ready");

    function glyphOf(marker) {
      return marker.querySelector(".map-marker__glyph");
    }

    function popoverOf(marker) {
      return marker.querySelector(".map-popover");
    }

    function isExpanded(marker) {
      var glyph = glyphOf(marker);
      return !!glyph && glyph.getAttribute("aria-expanded") === "true";
    }

    // Mobile/edge popover-overflow fix, JS half (requirement 5;
    // site/builders/map.py's `MarkerView.anchor_right` -- see
    // map_index.html's own CSS comment -- is the build-time half, a
    // deterministic left/right default that reduces how far off most
    // markers start). That default alone isn't sufficient on its own at
    // every viewport width -- e.g. a marker just past the 50% anchor
    // threshold, on a narrow phone, can still overflow the OPPOSITE edge
    // once its popover's real max-width is accounted for -- so this is
    // the authoritative runtime guarantee: after a popover opens, its
    // actual on-screen rect is measured against the real window bounds
    // and nudged back in with a small corrective translateX if needed.
    // Because .map-marker's own CSS counter-scales by exactly
    // `1 / var(--map-scale)` (see map_index.html's own comment on that
    // rule), one local CSS px inside a marker/popover always equals one
    // real screen px regardless of the map's current zoom level, so this
    // correction never needs to account for zoom itself.
    var EDGE_MARGIN = 8; // px breathing room from the true viewport edge

    function clampPopoverOnScreen(popover) {
      popover.style.transform = ""; // reset before measuring fresh
      var rect = popover.getBoundingClientRect();
      var maxRight = window.innerWidth - EDGE_MARGIN;
      var minLeft = EDGE_MARGIN;
      var maxBottom = window.innerHeight - EDGE_MARGIN;
      var minTop = EDGE_MARGIN;
      var dx = 0;
      var dy = 0;
      if (rect.right > maxRight) {
        dx = maxRight - rect.right;
      } else if (rect.left < minLeft) {
        dx = minLeft - rect.left;
      }
      if (rect.bottom > maxBottom) {
        dy = maxBottom - rect.bottom;
      } else if (rect.top < minTop) {
        dy = minTop - rect.top;
      }
      if (dx !== 0 || dy !== 0) {
        popover.style.transform = "translate(" + dx + "px, " + dy + "px)";
      }
    }

    function setExpanded(marker, expanded) {
      var glyph = glyphOf(marker);
      var popover = popoverOf(marker);
      if (!glyph || !popover) {
        return;
      }
      glyph.setAttribute("aria-expanded", expanded ? "true" : "false");
      if (expanded) {
        popover.removeAttribute("hidden");
        clampPopoverOnScreen(popover);
      } else {
        popover.setAttribute("hidden", "");
        popover.style.transform = "";
      }
    }

    // -----------------------------------------------------------------
    // Tap-vs-drag disambiguation (requirement 4). A single pointer's
    // movement between its own pointerdown and pointerup is tracked by
    // the pan/zoom controller below; if it exceeded DRAG_THRESHOLD, the
    // one-shot `suppressNextClick` flag set at that pointerup (see
    // endPointer) is consumed by whichever "click" event fires next --
    // normally the browser's own synthetic click for that same gesture,
    // if the pointerup landed back over the element the pointerdown
    // started on -- so a drag that happened to start on a marker's
    // glyph never also opens its popover. A single shared flag, not
    // per-element bookkeeping, so it applies uniformly to every marker
    // without each one needing its own pointer listeners; it's reset the
    // instant any NEW gesture begins (see onPointerDown), so it can never
    // bleed into a later, unrelated tap, and a keyboard-triggered click
    // (Enter/Space on a focused button) is never preceded by a pointerup
    // at all, so it's never affected either.
    // -----------------------------------------------------------------
    var DRAG_THRESHOLD = 8; // px
    // A one-shot flag, not a time window: set the instant a real drag/
    // pan gesture ends, consumed by whichever "click" event fires next
    // (the browser's own synthetic click for that same gesture, if the
    // pointerup landed back over the element the pointerdown started
    // on), then cleared -- so it can never suppress an unrelated later
    // tap, however soon after the drag it happens.
    var suppressNextClick = false;

    function wasProbablyADrag() {
      if (suppressNextClick) {
        suppressNextClick = false;
        return true;
      }
      return false;
    }

    for (var i = 0; i < markers.length; i++) {
      (function (marker) {
        var glyph = glyphOf(marker);
        if (!glyph) {
          return;
        }
        glyph.addEventListener("click", function (event) {
          if (wasProbablyADrag()) {
            event.preventDefault();
            return;
          }
          setExpanded(marker, !isExpanded(marker));
        });
      })(markers[i]);
    }

    var expandAllBtn = document.getElementById("map-expand-all");
    if (expandAllBtn) {
      var allExpanded = false;
      expandAllBtn.setAttribute("aria-pressed", "false");
      expandAllBtn.addEventListener("click", function () {
        allExpanded = !allExpanded;
        for (var j = 0; j < markers.length; j++) {
          setExpanded(markers[j], allExpanded);
        }
        expandAllBtn.setAttribute("aria-pressed", allExpanded ? "true" : "false");
        expandAllBtn.textContent = allExpanded ? "Collapse all" : "Expand all";
      });
    }

    var filterBtn = document.getElementById("map-filter-open-weights");
    if (filterBtn) {
      var filterActive = false;
      filterBtn.setAttribute("aria-pressed", "false");
      filterBtn.addEventListener("click", function () {
        filterActive = !filterActive;
        for (var k = 0; k < markers.length; k++) {
          var marker = markers[k];
          var isOpenWeights = marker.getAttribute("data-open-weights") === "true";
          if (filterActive && !isOpenWeights) {
            marker.classList.add("map-marker--dimmed");
          } else {
            marker.classList.remove("map-marker--dimmed");
          }
        }
        filterBtn.setAttribute("aria-pressed", filterActive ? "true" : "false");
      });
    }

    // -----------------------------------------------------------------
    // Pan + zoom (requirements 2/3/6). `viewport` is the fixed-size,
    // overflow:hidden clipping window (site/templates/map_index.html's
    // .map-viewport); `wrap` is the transformed content (the existing
    // .map-wrap, unchanged size/position CSS-wise -- see that rule's
    // own comment). State is a plain {scale, x, y}: `x`/`y` are the
    // wrap's translate offset in CSS px, `scale` its zoom factor, both
    // relative to the wrap's own natural (untransformed) top-left.
    // -----------------------------------------------------------------
    var viewport = document.getElementById("map-viewport");
    var wrap = document.getElementById("map-wrap");
    if (!viewport || !wrap) {
      return;
    }

    var MIN_SCALE = 1;
    var MAX_SCALE = 6;
    var WHEEL_ZOOM_RATIO = 1.15;
    var BUTTON_ZOOM_RATIO = 1.4;

    var state = { scale: MIN_SCALE, x: 0, y: 0 };

    // The wrap's own natural (scale 1) layout size never changes with
    // zoom (CSS `transform` doesn't affect layout, only paint) -- it's
    // always "100% of the viewport's width, aspect-ratio-locked height"
    // per map_index.html's own .map-wrap rule. Reading it once up front
    // (before any transform is ever applied) and re-deriving it from
    // the viewport's current width on resize is cheaper and more
    // robust than re-measuring a possibly-already-transformed element.
    var naturalWidth = 0;
    var naturalHeight = 0;
    var naturalAspect = 1;

    function measureNatural() {
      var rect = wrap.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        naturalWidth = rect.width;
        naturalHeight = rect.height;
        naturalAspect = naturalWidth / naturalHeight;
      }
    }

    function refreshNaturalWidth() {
      // Only the width can change (the viewport's own width is fluid);
      // the aspect ratio is fixed by .map-wrap's CSS `aspect-ratio`.
      var vpRect = viewport.getBoundingClientRect();
      if (vpRect.width > 0) {
        naturalWidth = vpRect.width;
        naturalHeight = naturalWidth / naturalAspect;
      }
    }

    measureNatural();

    function clampPan(nextState) {
      var scaledW = naturalWidth * nextState.scale;
      var scaledH = naturalHeight * nextState.scale;
      var vpRect = viewport.getBoundingClientRect();
      var vpW = vpRect.width;
      var vpH = vpRect.height;

      var minX, maxX, minY, maxY;
      if (scaledW <= vpW) {
        minX = maxX = (vpW - scaledW) / 2;
      } else {
        minX = vpW - scaledW;
        maxX = 0;
      }
      if (scaledH <= vpH) {
        minY = maxY = (vpH - scaledH) / 2;
      } else {
        minY = vpH - scaledH;
        maxY = 0;
      }
      nextState.x = clamp(nextState.x, minX, maxX);
      nextState.y = clamp(nextState.y, minY, maxY);
    }

    function applyTransform() {
      wrap.style.transform =
        "translate(" + state.x + "px, " + state.y + "px) scale(" + state.scale + ")";
      wrap.style.setProperty("--map-scale", String(state.scale));
    }

    function commit(nextState, opts) {
      clampPan(nextState);
      state.scale = nextState.scale;
      state.x = nextState.x;
      state.y = nextState.y;
      if (opts && opts.animate) {
        wrap.classList.add("map-transition");
      } else {
        wrap.classList.remove("map-transition");
      }
      applyTransform();
    }

    function fillScale() {
      var vpRect = viewport.getBoundingClientRect();
      if (!naturalHeight) {
        return MIN_SCALE;
      }
      // "Bigger" (requirement 1): the default/reset view fills at least
      // the viewport's own height (the wrap already fills its width by
      // construction), clamped to the same 1x-6x range every other zoom
      // action respects, rather than a separate unbounded computation.
      return clamp(vpRect.height / naturalHeight, MIN_SCALE, MAX_SCALE);
    }

    function resetView(opts) {
      refreshNaturalWidth();
      var scale = fillScale();
      var vpRect = viewport.getBoundingClientRect();
      var next = {
        scale: scale,
        x: (vpRect.width - naturalWidth * scale) / 2,
        y: (vpRect.height - naturalHeight * scale) / 2,
      };
      commit(next, opts);
    }

    // zoomAt: keep the content point currently under viewport-relative
    // (cx, cy) fixed on screen while scaling by `ratio` -- the standard
    // "zoom toward a point" transform, used for wheel zoom (toward the
    // cursor), pinch zoom (toward the pinch midpoint), and the +/-
    // buttons (toward the viewport's own center).
    function zoomAt(cx, cy, ratio, opts) {
      var oldScale = state.scale;
      var newScale = clamp(oldScale * ratio, MIN_SCALE, MAX_SCALE);
      if (newScale === oldScale) {
        return;
      }
      var contentX = (cx - state.x) / oldScale;
      var contentY = (cy - state.y) / oldScale;
      commit(
        {
          scale: newScale,
          x: cx - contentX * newScale,
          y: cy - contentY * newScale,
        },
        opts
      );
    }

    resetView({ animate: false });

    // --- Explicit +/-/reset controls (requirement 6: real, keyboard-
    // operable <button>s -- "click" fires for both pointer and
    // keyboard activation, so no separate keydown handling is needed).
    var zoomInBtn = document.getElementById("map-zoom-in");
    var zoomOutBtn = document.getElementById("map-zoom-out");
    var zoomResetBtn = document.getElementById("map-zoom-reset");

    function viewportCenter() {
      var vpRect = viewport.getBoundingClientRect();
      return { x: vpRect.width / 2, y: vpRect.height / 2 };
    }

    if (zoomInBtn) {
      zoomInBtn.addEventListener("click", function () {
        var c = viewportCenter();
        zoomAt(c.x, c.y, BUTTON_ZOOM_RATIO, { animate: true });
      });
    }
    if (zoomOutBtn) {
      zoomOutBtn.addEventListener("click", function () {
        var c = viewportCenter();
        zoomAt(c.x, c.y, 1 / BUTTON_ZOOM_RATIO, { animate: true });
      });
    }
    if (zoomResetBtn) {
      zoomResetBtn.addEventListener("click", function () {
        resetView({ animate: true });
      });
    }

    // --- Mouse-wheel zoom toward the cursor (desktop).
    viewport.addEventListener(
      "wheel",
      function (event) {
        event.preventDefault();
        var vpRect = viewport.getBoundingClientRect();
        var cx = event.clientX - vpRect.left;
        var cy = event.clientY - vpRect.top;
        var ratio = event.deltaY < 0 ? WHEEL_ZOOM_RATIO : 1 / WHEEL_ZOOM_RATIO;
        zoomAt(cx, cy, ratio, { animate: false });
      },
      { passive: false }
    );

    // --- Drag-to-pan (mouse) + single-finger drag-to-pan (touch) +
    // two-pointer pinch-to-zoom, all via the unified Pointer Events API
    // so the same code path handles mouse, touch, and pen. `touch-action:
    // none` on .map-viewport (CSS) is what stops a touch drag from also
    // scrolling the page (requirement 3); `preventDefault()` below on an
    // actual drag/pinch move additionally stops touch text-selection/
    // refresh-gesture side effects some browsers still apply.
    var activePointers = {}; // pointerId -> {x, y}
    var activeCount = 0;
    var dragState = null; // {pointerId, startX, startY, originX, originY, moved}
    var pinchState = null; // {lastDist}

    function pointerList() {
      var ids = Object.keys(activePointers);
      return [activePointers[ids[0]], activePointers[ids[1]]];
    }

    function distanceBetween(a, b) {
      return Math.hypot(a.x - b.x, a.y - b.y);
    }

    function midpointOf(a, b) {
      return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    }

    function onPointerDown(event) {
      // Never start a pan/pinch gesture from a click on a real control
      // (marker glyph, zoom buttons, toggle buttons, name link) -- let
      // those handle their own click normally; the viewport-level drag
      // only concerns dragging the empty map canvas itself. Multi-touch
      // pinch is the one exception: once a second pointer lands, treat
      // it as a pinch regardless of what the first pointer started on,
      // since a real two-finger pinch gesture is unambiguous.
      // .map-popover (its content can scroll -- see the CSS `touch-action:
      // pan-y` override) and the small tap-only .map-zoom-btn/.map-toggle
      // buttons are excluded from pan tracking entirely. The marker
      // glyph and name link are deliberately NOT excluded: a drag
      // gesture is allowed to start on top of a marker (it should still
      // pan the map, matching ordinary map UX), it just must not ALSO
      // open/navigate that marker -- see wasProbablyADrag() above, which
      // is exactly what makes that safe to allow here.
      var startedOnExcluded =
        event.target.closest &&
        event.target.closest(".map-zoom-btn, .map-toggle, .map-popover");
      if (startedOnExcluded && activeCount === 0) {
        return;
      }

      activePointers[event.pointerId] = { x: event.clientX, y: event.clientY };
      activeCount = Object.keys(activePointers).length;

      if (activeCount === 1) {
        // A brand-new gesture starting invalidates any leftover
        // suppression armed by a PREVIOUS, already-finished gesture
        // (its own following click either already fired and consumed
        // it, or was never going to fire at all) -- so suppression can
        // never bleed into an unrelated later tap.
        suppressNextClick = false;
        dragState = {
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          originX: state.x,
          originY: state.y,
          moved: 0,
          captured: false,
        };
        // Deliberately NOT calling setPointerCapture here yet: capturing
        // eagerly on every pointerdown (including ones that start on a
        // marker glyph or the zoom buttons) redirects the eventual
        // pointerup -- and with it the browser's synthesized "click" --
        // away from that descendant element, silently breaking a plain
        // tap/click. Capture is instead requested lazily in
        // onPointerMove, the moment a gesture actually crosses
        // DRAG_THRESHOLD and is confirmed to be a real drag, so an
        // ordinary tap is never touched by it at all.
      } else if (activeCount === 2) {
        dragState = null; // a second finger landing cancels any single-pointer drag/pan
        var pts = pointerList();
        pinchState = { lastDist: distanceBetween(pts[0], pts[1]) };
      }
    }

    function onPointerMove(event) {
      if (!(event.pointerId in activePointers)) {
        return;
      }
      activePointers[event.pointerId] = { x: event.clientX, y: event.clientY };

      if (activeCount >= 2) {
        var pts = pointerList();
        var dist = distanceBetween(pts[0], pts[1]);
        var mid = midpointOf(pts[0], pts[1]);
        var vpRect = viewport.getBoundingClientRect();
        if (pinchState && pinchState.lastDist) {
          var ratio = dist / pinchState.lastDist;
          zoomAt(mid.x - vpRect.left, mid.y - vpRect.top, ratio, { animate: false });
        }
        if (pinchState) {
          pinchState.lastDist = dist;
        }
        event.preventDefault();
        return;
      }

      if (dragState && dragState.pointerId === event.pointerId) {
        var dx = event.clientX - dragState.startX;
        var dy = event.clientY - dragState.startY;
        dragState.moved = Math.max(dragState.moved, Math.hypot(dx, dy));
        if (dragState.moved > DRAG_THRESHOLD) {
          if (!dragState.captured) {
            dragState.captured = true;
            try {
              viewport.setPointerCapture(event.pointerId);
            } catch (err) {
              /* pointer capture is a nice-to-have; ignore if unsupported */
            }
          }
          viewport.classList.add("is-panning");
          commit(
            { scale: state.scale, x: dragState.originX + dx, y: dragState.originY + dy },
            { animate: false }
          );
          event.preventDefault();
        }
      }
    }

    function endPointer(event) {
      delete activePointers[event.pointerId];
      activeCount = Object.keys(activePointers).length;

      if (dragState && dragState.pointerId === event.pointerId) {
        if (dragState.moved > DRAG_THRESHOLD) {
          // Consumed by the very next click (see wasProbablyADrag above)
          // so a marker glyph under the pointer doesn't also toggle.
          suppressNextClick = true;
        }
        dragState = null;
        viewport.classList.remove("is-panning");
      }
      if (activeCount < 2) {
        pinchState = null;
      }
      if (activeCount === 0) {
        try {
          viewport.releasePointerCapture(event.pointerId);
        } catch (err) {
          /* already released / unsupported; ignore */
        }
      }
    }

    viewport.addEventListener("pointerdown", onPointerDown);
    viewport.addEventListener("pointermove", onPointerMove);
    viewport.addEventListener("pointerup", endPointer);
    viewport.addEventListener("pointercancel", endPointer);

    // --- Resize handling: re-derive the natural size from the (fluid-
    // width) viewport and re-clamp the current pan/zoom into bounds so
    // a rotate/resize never leaves the map panned out of view.
    var resizeQueued = false;
    window.addEventListener("resize", function () {
      if (resizeQueued) {
        return;
      }
      resizeQueued = true;
      window.requestAnimationFrame(function () {
        resizeQueued = false;
        refreshNaturalWidth();
        commit({ scale: state.scale, x: state.x, y: state.y }, { animate: false });
      });
    });
  });
})();
