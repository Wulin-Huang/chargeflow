import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";

export default function EChart({
  option,
  height = 260,
}: {
  option: EChartsOption;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chartRef.current = chart;
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: false });
  }, [option]);

  return <div ref={ref} style={{ height }} />;
}

export const AXIS = {
  axisLine: { lineStyle: { color: "#dbe4f0" } },
  axisLabel: { color: "#62708c", fontSize: 11 },
  splitLine: { lineStyle: { color: "#eef2f8" } },
};

export const TOOLTIP = {
  backgroundColor: "#ffffff",
  borderColor: "#e4e9f2",
  textStyle: { color: "#16213a", fontSize: 12 },
  extraCssText: "box-shadow: 0 8px 24px rgba(22,33,58,0.14); border-radius: 10px;",
};
