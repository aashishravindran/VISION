import Chart from "@/components/Chart";

export default async function ChartPage({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  const { ticker } = await params;
  return (
    <div>
      <Chart ticker={ticker.toUpperCase()} />
    </div>
  );
}
