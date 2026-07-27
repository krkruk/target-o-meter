// S-02 Phase 7: DailyAverageChart — recharts LineChart of the past month's
// daily average. The first recharts usage in the project.
//
// recharts renders SVGs that aren't screen-reader-friendly by default, so the
// wrapper carries role="img" + an aria-label summarizing the data (min/max/
// mean of the mocked series). Reads from the mocked fixture (aggregation is
// S-03); ResponsiveContainer sizes the chart to its grid area.
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { mockDailyAverages } from '../mocks/dashboard';
import styles from './DailyAverageChart.module.css';

export function DailyAverageChart() {
  const data = mockDailyAverages;
  const averages = data.map((d) => d.average);
  const min = Math.min(...averages).toFixed(1);
  const max = Math.max(...averages).toFixed(1);
  const mean = (averages.reduce((a, b) => a + b, 0) / averages.length).toFixed(1);
  const first = data[0].date;
  const last = data[data.length - 1].date;

  return (
    <div
      className={styles.wrapper}
      role="img"
      aria-label={
        `Daily average chart for ${first} to ${last}. ` +
        `Min ${min}, max ${max}, mean ${mean} out of 10.`
      }
    >
      <h3 className={styles.heading}>Daily average — past 30 days</h3>
      <ResponsiveContainer width="100%" height="100%" minHeight={160}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: -16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
          <XAxis dataKey="date" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis domain={[0, 10]} tick={{ fontSize: 10 }} />
          <Tooltip />
          <Line
            type="monotone"
            dataKey="average"
            stroke="var(--color-primary)"
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
