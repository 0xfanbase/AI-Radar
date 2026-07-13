/*
 * World-map marker interaction -- click/expand popovers, "expand all",
 * and the open-weights filter toggle.
 *
 * This is the site's SECOND deliberate, narrow exception to an
 * otherwise zero-JavaScript architecture (the first is
 * site/static/js/matrix-rain.js -- see that file's own header comment
 * and IMPROVEMENT_BACKLOG.md for the owner sign-off ritual this repeats).
 * It is only ever included on the map homepage (site/templates/
 * map_index.html), never on any other page -- see
 * site/tests/test_build.py's script-tag allowlist test.
 *
 * Progressive enhancement, for real: every marker's company-NAME text
 * (`.map-marker__name`) is a plain `<a href="/companies/<slug>/">`
 * rendered directly by site/builders/map.py/templates/map_index.html at
 * build time -- reachable and readable with this script never loading,
 * blocked, or failing partway through. This script attaches behavior
 * ONLY to the separate marker GLYPH button (`.map-marker__glyph`) and
 * the two toggle buttons above the map; it never rewrites, hides, or
 * gates the name link itself. If this file fails to run for any reason,
 * every popover simply stays collapsed (its `hidden` attribute, set by
 * the server-rendered HTML, is native and requires no script to take
 * effect) and the two toggle buttons are inert -- the map is still
 * fully readable and every company page is still one tap away via its
 * name link.
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

  ready(function () {
    var markers = document.querySelectorAll(".map-marker");
    if (!markers.length) {
      return;
    }

    // Flip the CSS "is this script actually running" flag (see
    // map_index.html's own .map-toggle / body.map-js-ready rule) --
    // only reached once we know a script environment exists and this
    // file itself is executing, not merely that the DOM is ready.
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

    function setExpanded(marker, expanded) {
      var glyph = glyphOf(marker);
      var popover = popoverOf(marker);
      if (!glyph || !popover) {
        return;
      }
      glyph.setAttribute("aria-expanded", expanded ? "true" : "false");
      if (expanded) {
        popover.removeAttribute("hidden");
      } else {
        popover.setAttribute("hidden", "");
      }
    }

    for (var i = 0; i < markers.length; i++) {
      (function (marker) {
        var glyph = glyphOf(marker);
        if (!glyph) {
          return;
        }
        glyph.addEventListener("click", function () {
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
  });
})();
