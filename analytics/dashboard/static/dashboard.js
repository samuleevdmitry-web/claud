// Графики дашборда: данные приходят с сервера в <script id="chart-data">, цвета — из CSS-переменных.
(function () {
  const data = JSON.parse(document.getElementById("chart-data").textContent);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const nf = new Intl.NumberFormat("ru-RU");

  function options(spec) {
    const ink = { muted: css("--text-muted"), secondary: css("--text-secondary"), primary: css("--text-primary") };
    const colors = [css("--series-1"), css("--series-2"), css("--series-3")];
    const isBar = spec.kind === "bar";
    const many = spec.series.length > 1;
    const series = spec.series.map((s, i) => ({
      name: s.name,
      type: spec.kind,
      data: s.data,
      color: colors[i % colors.length],
      barMaxWidth: 24,
      itemStyle: isBar ? { borderRadius: [4, 4, 0, 0] } : undefined,
      lineStyle: isBar ? undefined : { width: 2, cap: "round", join: "round" },
      symbol: "circle",
      symbolSize: 8,
      showSymbol: false,
      emphasis: { focus: "none" },
      // выбранный месяц — тонкая вертикальная линия, не цвет
      markLine: i === 0 && spec.selected ? {
        silent: true, symbol: "none", label: { show: false },
        lineStyle: { color: css("--axis"), width: 1, type: "solid" },
        data: [{ xAxis: spec.selected }],
      } : undefined,
    }));
    return {
      animation: false,
      grid: { left: 8, right: 16, top: many ? 32 : 12, bottom: 4, containLabel: true },
      legend: many ? { top: 0, left: 0, icon: isBar ? "roundRect" : "path://M0,0 L14,0 L14,2 L0,2 Z", itemWidth: 14, itemHeight: isBar ? 10 : 2,
                       textStyle: { color: ink.secondary } } : undefined,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: isBar ? "shadow" : "line", lineStyle: { color: css("--axis") } },
        backgroundColor: css("--surface"),
        borderColor: css("--border"),
        textStyle: { color: ink.primary },
        valueFormatter: (v) => (v == null ? "—" : nf.format(Math.round(v))),
      },
      xAxis: {
        type: "category", data: spec.labels,
        axisLine: { lineStyle: { color: css("--axis") } }, axisTick: { show: false },
        axisLabel: { color: ink.muted, hideOverlap: true },
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { color: css("--grid"), width: 1 } },
        axisLabel: { color: ink.muted, formatter: (v) => (v >= 10000 ? nf.format(v / 1000) + " тыс." : nf.format(v)) },
      },
      series,
    };
  }

  const charts = [];
  document.querySelectorAll("[data-chart]").forEach((el) => {
    const spec = data[el.dataset.chart];
    if (!spec) return;
    const chart = echarts.init(el, null, { renderer: "svg" });
    chart.setOption(options(spec));
    charts.push([chart, spec]);
  });
  window.addEventListener("resize", () => charts.forEach(([c]) => c.resize()));
  // смена светлой/тёмной темы — перерисовать с новыми цветами
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () =>
    charts.forEach(([c, spec]) => c.setOption(options(spec), true)));
})();
