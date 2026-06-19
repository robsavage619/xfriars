/* xFriars shared baseball geometry kit — build once, reuse on every spatial card.
 *
 * Editorial house-style: home-plate pentagon + outfield arc + two foul lines only.
 * No infield, grass, dirt, or fence detail — the brain completes the field.
 * All chrome is thin brown hairlines. Angles are preserved (uniform scale), so the
 * 45° foul lines read true. Coordinates are field-feet with home plate at (0,0),
 * +y toward center field, +x toward the right-field line (Statcast convention after
 * the (hc_x-125.42)*2.5 / (198.27-hc_y)*2.5 transform).
 */
window.Baseball = (function () {
  "use strict";

  var SQRT1_2 = Math.SQRT1_2;

  /* Set up a uniform field-feet → pixel projection and draw the field chrome.
   * Returns { toPx, s } so the caller can plot events in the same space. */
  function spray(svg, W, H, opts) {
    opts = opts || {};
    var brown = opts.brown || "#4A3526";
    var Xmax = opts.xmax || 255; // half-width of plotted ground, feet
    var Ymax = opts.ymax || 420; // depth to plot, feet
    var R = opts.arc || 400; // outfield arc radius, feet
    var padBottom = opts.padBottom != null ? opts.padBottom : 30;
    var padTop = opts.padTop != null ? opts.padTop : 10;

    var s = Math.min(W / (2 * Xmax), (H - padBottom - padTop) / Ymax);
    var ox = W / 2;
    var oy = H - padBottom; // home-plate pixel y

    function toPx(x, y) {
      return [ox + x * s, oy - y * s];
    }

    var home = toPx(0, 0);
    var lf = toPx(-R * SQRT1_2, R * SQRT1_2); // left-field pole
    var rf = toPx(R * SQRT1_2, R * SQRT1_2); // right-field pole
    var rpx = R * s;

    // Outfield arc (minor arc bulging through center field).
    svg
      .append("path")
      .attr("d", "M " + lf[0] + " " + lf[1] + " A " + rpx + " " + rpx + " 0 0 1 " + rf[0] + " " + rf[1])
      .attr("fill", "none")
      .attr("stroke", brown)
      .attr("stroke-width", 1.5)
      .attr("stroke-opacity", 0.5);

    // Foul lines.
    [lf, rf].forEach(function (p) {
      svg
        .append("line")
        .attr("x1", home[0])
        .attr("y1", home[1])
        .attr("x2", p[0])
        .attr("y2", p[1])
        .attr("stroke", brown)
        .attr("stroke-width", 1)
        .attr("stroke-opacity", 0.45);
    });

    // Home-plate pentagon (small, point down).
    var hp = 7;
    var hx = home[0];
    var hy = home[1];
    svg
      .append("path")
      .attr(
        "d",
        "M " + (hx - hp) + " " + (hy - hp) + " L " + (hx + hp) + " " + (hy - hp) +
          " L " + (hx + hp) + " " + hy + " L " + hx + " " + (hy + hp) + " L " + (hx - hp) + " " + hy + " Z",
      )
      .attr("fill", brown)
      .attr("fill-opacity", 0.85);

    return { toPx: toPx, s: s };
  }

  return { spray: spray };
})();
