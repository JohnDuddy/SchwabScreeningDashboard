import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const years = Array.from({ length: 10 }, (_, i) => 2015 + i);

const stateText = `01|Alabama|9.74/10.18|11.18/11.75|12.34/12.89|11.61/12.12|11.96/12.75|12.99/13.72|14.81/15.81|13.91/14.93|14.01/14.79|12.51/13.31
02|Alaska|8.40/7.92|7.27/7.46|10.53/10.49|7.60/7.51|10.63/10.82|7.50/7.33|6.67/6.35|10.35/10.15|8.28/8.30|7.57/7.58
04|Arizona|5.33/5.50|6.05/6.23|6.26/6.47|5.86/6.07|5.68/5.87|7.31/7.69|7.73/8.14|8.53/9.06|7.11/7.49|6.16/6.44
05|Arkansas|7.28/7.38|8.29/8.67|9.26/9.80|8.76/9.02|8.94/9.33|12.31/12.97|11.07/11.67|11.32/11.81|10.59/11.32|8.00/8.43
06|California|5.11/5.03|5.30/5.22|5.14/5.11|4.79/4.74|4.55/4.53|5.99/6.01|6.37/6.40|5.89/5.91|5.03/5.04|4.48/4.53
08|Colorado|3.78/3.71|4.24/4.25|4.65/4.59|4.62/4.66|4.34/4.30|5.81/5.85|6.33/6.33|7.13/7.20|6.24/6.19|5.12/5.11
09|Connecticut|3.46/3.64|2.46/2.65|3.05/3.21|2.57/2.71|2.97/3.15|4.25/4.54|4.44/4.78|4.06/4.32|4.20/4.51|2.83/3.05
10|Delaware|6.90/7.49|6.63/7.03|6.58/6.89|5.90/6.80|5.43/6.14|8.67/9.89|10.25/11.23|6.27/6.96|5.31/5.90|6.37/6.90
12|Florida|5.98/6.29|6.27/6.73|6.05/6.40|6.19/6.56|6.21/6.63|7.09/7.74|6.72/7.33|6.58/7.18|5.94/6.42|5.06/5.46
13|Georgia|7.25/7.28|7.82/7.87|7.78/7.80|7.55/7.68|7.99/8.07|10.18/10.45|11.18/11.42|11.19/11.33|9.64/9.83|8.95/9.14
15|Hawaii|2.18/2.21|2.73/2.84|2.46/2.52|2.81/3.03|2.47/2.50|3.17/3.13|2.70/2.67|2.92/3.00|2.91/2.94|2.70/2.82
16|Idaho|2.00/2.00|1.78/1.96|2.91/3.05|2.34/2.30|1.51/1.71|2.27/2.43|2.15/2.23|2.52/2.74|2.49/2.72|1.80/1.78
17|Illinois|6.71/6.80|9.02/9.17|8.76/8.99|7.81/7.99|7.73/8.01|10.57/10.99|11.71/12.21|10.40/10.87|9.26/9.72|8.32/8.69
18|Indiana|5.88/6.03|7.23/7.52|6.85/7.08|7.06/7.41|6.92/7.20|9.13/9.65|9.16/9.58|8.08/8.39|7.60/7.95|6.96/7.29
19|Iowa|2.34/2.43|2.71/2.84|3.31/3.43|2.57/2.76|2.53/2.73|3.32/3.56|2.94/3.18|2.81/2.91|3.14/3.33|3.24/3.43
20|Kansas|4.54/4.65|5.05/5.26|6.36/6.42|5.49/5.85|4.70/4.88|6.64/6.94|6.13/6.45|5.48/5.77|6.30/6.55|5.25/5.64
21|Kentucky|5.64/5.69|6.80/7.10|6.96/7.24|5.78/6.09|5.59/5.93|8.96/9.46|9.05/9.52|7.70/8.27|6.94/7.38|6.84/6.96
22|Louisiana|12.19/12.40|13.84/14.22|13.97/14.32|12.82/13.26|13.95/14.56|18.77/19.72|20.38/21.24|18.77/19.85|18.24/19.26|14.33/15.17
23|Maine|1.66/1.72|1.43/1.33|1.42/1.47|1.42/1.49|1.71/1.84|1.54/1.56|1.45/1.73|2.30/2.62|3.43/3.46|2.42/2.60
24|Maryland|9.95/10.27|9.64/10.03|9.74/10.15|8.95/9.20|9.55/9.96|10.51/11.12|11.47/12.17|10.63/11.31|9.36/9.99|8.45/9.00
25|Massachusetts|2.12/2.14|2.01/2.04|2.49/2.60|2.30/2.33|2.28/2.32|2.62/2.62|2.29/2.26|2.44/2.48|2.58/2.65|2.14/2.24
26|Michigan|6.01/6.40|6.27/6.58|5.89/6.16|6.14/6.38|6.13/6.40|8.05/8.58|8.19/8.67|7.97/8.56|6.32/6.79|5.31/5.70
27|Minnesota|2.68/2.79|2.34/2.39|2.19/2.24|2.18/2.31|2.73/2.83|3.47/3.58|4.06/4.31|3.62/3.79|3.42/3.58|3.31/3.45
28|Mississippi|10.87/11.18|11.54/11.98|12.04/12.61|12.81/13.35|14.57/15.39|19.47/20.56|22.26/23.61|19.58/20.75|18.45/19.36|18.62/19.66
29|Missouri|9.00/9.51|9.36/9.87|10.72/11.19|10.74/11.37|10.23/10.79|13.05/13.83|11.60/12.36|12.04/12.80|9.86/10.41|8.89/9.36
30|Montana|3.68/3.96|4.03/4.30|3.99/4.31|3.96/4.19|3.64/3.70|5.98/6.61|4.16/4.37|5.08/5.39|4.42/4.56|4.22/4.59
31|Nebraska|3.96/4.02|3.15/3.34|2.61/2.73|1.82/1.88|3.00/3.10|3.87/3.98|3.56/3.59|3.70/3.75|3.22/3.30|2.94/3.08
32|Nevada|6.66/6.75|7.23/7.43|7.44/7.65|7.42/7.67|5.37/5.50|6.96/7.35|8.39/8.47|7.68/7.85|8.03/8.21|6.76/7.02
33|New Hampshire|1.35/1.44|1.34/1.26|1.26/1.40|1.55/1.72|2.79/2.89|1.02/1.05|1.08/1.07|1.79/1.77|2.07/1.94|1.28/1.45
34|New Jersey|4.37/4.57|4.45/4.67|3.96/4.15|3.50/3.68|3.23/3.45|3.90/4.14|4.41/4.76|3.57/3.81|3.12/3.33|2.78/2.93
35|New Mexico|7.51/7.91|8.84/9.39|8.27/8.52|10.27/10.79|10.95/11.75|10.20/10.70|14.45/15.30|13.72/14.60|13.67/14.75|12.58/13.16
36|New York|3.41/3.41|3.54/3.59|2.95/2.97|3.13/3.17|3.09/3.15|4.35/4.45|4.63/4.81|4.33/4.48|3.78/3.92|3.49/3.54
37|North Carolina|5.91/6.10|7.23/7.36|6.61/6.81|6.23/6.37|6.73/6.96|8.46/8.72|9.38/9.67|8.91/9.23|8.15/8.47|7.55/7.82
38|North Dakota|2.91/3.17|2.25/2.24|1.98/2.03|2.63/2.53|2.75/3.06|3.85/4.25|3.09/3.40|3.59/3.48|3.04/3.17|2.51/2.51
39|Ohio|5.76/5.99|6.19/6.49|7.10/7.51|6.51/6.83|6.19/6.56|8.51/9.03|8.67/9.34|7.89/8.49|7.35/7.88|6.26/6.61
40|Oklahoma|8.29/8.46|8.20/8.60|8.08/8.54|6.72/6.98|8.41/8.75|8.63/9.05|8.57/8.93|7.87/8.30|7.21/7.53|6.91/7.19
41|Oregon|3.43/3.43|3.15/3.22|3.06/3.09|2.44/2.43|2.99/2.96|3.70/3.76|4.80/4.91|4.99/5.10|4.42/4.55|4.00/4.01
42|Pennsylvania|5.26/5.50|5.65/5.95|6.18/6.59|6.10/6.37|5.64/6.05|7.65/8.34|8.46/9.11|8.23/8.91|7.02/7.56|5.23/5.56
44|Rhode Island|2.65/2.77|2.36/2.25|1.80/1.97|1.51/1.39|2.17/2.53|2.65/2.78|3.65/3.52|2.00/1.98|2.72/2.52|1.80/2.04
45|South Carolina|9.15/9.40|8.58/8.97|8.83/9.25|9.45/10.04|10.22/10.85|12.12/12.85|12.63/13.38|11.25/11.88|10.47/11.25|9.22/9.90
46|South Dakota|4.10/4.37|4.40/4.75|3.89/4.22|3.64/3.87|3.27/3.55|5.86/6.50|5.02/5.35|6.16/6.81|4.14/4.30|5.95/6.69
47|Tennessee|6.98/7.13|8.47/8.71|8.30/8.77|8.91/9.21|8.73/9.12|10.87/11.33|11.63/12.23|10.45/11.03|10.87/11.36|9.64/10.05
48|Texas|5.60/5.59|5.98/5.96|5.84/5.78|5.44/5.45|5.84/5.86|7.57/7.59|8.09/8.14|7.58/7.58|7.03/7.02|5.92/5.92
49|Utah|2.01/1.92|2.63/2.53|2.55/2.58|2.12/2.21|2.56/2.57|2.89/2.89|2.73/2.68|2.12/2.16|2.21/2.21|2.34/2.29
50|Vermont|2.56/2.67|1.76/1.90|2.40/2.47|2.24/2.45|1.76/1.93|2.18/2.62|1.55/1.56|2.78/3.09|3.24/3.41|3.24/3.19
51|Virginia|4.47/4.46|5.41/5.48|5.37/5.36|4.99/5.09|5.10/5.24|6.15/6.32|7.00/7.20|7.52/7.81|6.59/6.72|5.41/5.64
53|Washington|3.34/3.37|2.96/2.92|3.58/3.60|3.65/3.69|3.17/3.20|4.17/4.20|4.47/4.55|5.44/5.46|5.27/5.31|4.32/4.41
54|West Virginia|4.34/4.52|5.89/6.27|6.16/6.46|5.37/5.77|5.13/5.65|6.36/6.98|6.38/6.86|5.92/6.28|4.97/5.34|5.48/5.84
55|Wisconsin|4.22/4.45|4.43/4.75|3.49/3.70|3.51/3.88|3.88/4.15|5.66/5.96|5.92/6.35|5.54/6.02|4.79/5.11|4.46/4.80
56|Wyoming|2.90/2.89|2.91/3.05|3.28/3.60|3.80/4.18|4.31/4.47|4.33/4.80|2.76/3.06|2.92/3.14|3.08/3.23|4.60/4.82`;

const nationalText = `2015|17793|320738994|5.55|5.64
2016|19362|323071755|5.99|6.13
2017|19510|325122128|6.00|6.14
2018|18830|326838199|5.76|5.91
2019|19141|328329953|5.83|6.01
2020|24576|331577720|7.41|7.69
2021|26031|332099760|7.84|8.15
2022|24849|334017321|7.44|7.75
2023|22829|336806231|6.78|7.04
2024|20162|340110988|5.93|6.15`;

const states = stateText.trim().split("\n").map((line) => {
  const [fips, state, ...pairs] = line.split("|");
  const rates = pairs.map((pair) => {
    const [crude, adjusted] = pair.split("/").map(Number);
    return { crude, adjusted };
  });
  return { fips, state, rates };
});
const national = nationalText.trim().split("\n").map((line) => {
  const [year, deaths, population, crude, adjusted] = line.split("|").map(Number);
  return { year, deaths, population, crude, adjusted };
});

const wb = Workbook.create();
const dashboard = wb.worksheets.add("Dashboard");
const ratesSheet = wb.worksheets.add("State Rates");
const longSheet = wb.worksheets.add("Long Data");
const nationalSheet = wb.worksheets.add("National");
const notes = wb.worksheets.add("Notes & Sources");
for (const sheet of [dashboard, ratesSheet, longSheet, nationalSheet, notes]) sheet.showGridLines = false;

const navy = "#15324B";
const teal = "#138A8A";
const paleTeal = "#DFF3F1";
const paleBlue = "#EAF1F7";
const gold = "#E6A93D";
const red = "#C84A4A";
const ink = "#1F2937";
const muted = "#607080";
const border = "#D6DEE5";
const white = "#FFFFFF";

function titleBand(sheet, range, title, subtitle) {
  sheet.getRange(range).merge();
  const topLeft = range.split(":")[0];
  sheet.getRange(topLeft).values = [[title]];
  sheet.getRange(range).format = {
    fill: navy,
    font: { color: white, bold: true, size: 20 },
    verticalAlignment: "center",
  };
  const startRow = Number(topLeft.match(/\d+/)[0]) + 2;
  const startCol = topLeft.match(/[A-Z]+/)[0];
  sheet.getRange(`${startCol}${startRow}`).values = [[subtitle]];
  sheet.getRange(`${startCol}${startRow}`).format = { font: { color: muted, italic: true, size: 10 } };
}

// Dashboard
titleBand(
  dashboard,
  "A1:N2",
  "U.S. Homicide Mortality by State, 2015–2024",
  "CDC WISQARS / NVSS • crude deaths per 100,000 residents • 50 states (D.C. excluded)",
);
dashboard.getRange("A4:B4").values = [["2024 U.S. rate", national.at(-1).crude]];
dashboard.getRange("D4:E4").values = [["2021 peak rate", national.find((d) => d.year === 2021).crude]];
dashboard.getRange("A6:B6").values = [["Change, 2021→2024", (national.at(-1).crude / national.find((d) => d.year === 2021).crude) - 1]];
dashboard.getRange("D6:E6").values = [["Change, 2015→2024", (national.at(-1).crude / national[0].crude) - 1]];
for (const r of ["A4:B4", "D4:E4", "A6:B6", "D6:E6"]) {
  dashboard.getRange(r).format = {
    fill: paleBlue,
    font: { color: ink, bold: true },
    borders: { preset: "outside", style: "thin", color: border },
    verticalAlignment: "center",
  };
}
dashboard.getRange("B4:E4").format.numberFormat = "0.00";
dashboard.getRange("B6:E6").format.numberFormat = "0.0%";
dashboard.getRange("A4:E6").format.rowHeight = 28;
dashboard.getRange("A8:F8").merge();
dashboard.getRange("A8").values = [["The national rate rose sharply in 2020–2021, then fell for three straight years."]];
dashboard.getRange("A8:F8").format = { fill: paleTeal, font: { color: navy, bold: true }, wrapText: true };

dashboard.getRange("H4:I14").values = [
  ["Year", "U.S. crude rate"],
  ...national.map((d) => [d.year, d.crude]),
];
dashboard.getRange("H4:I4").format = { fill: teal, font: { color: white, bold: true } };
dashboard.getRange("I5:I14").format.numberFormat = "0.00";
const nationalChart = dashboard.charts.add("line", dashboard.getRange("H4:I14"));
nationalChart.title = "U.S. homicide mortality rate";
nationalChart.hasLegend = false;
nationalChart.xAxis = { axisType: "textAxis", title: { text: "Year" } };
nationalChart.yAxis = { numberFormatCode: "0.0", min: 0, title: { text: "Deaths per 100,000" } };
nationalChart.setPosition("A10", "G25");

const top10 = [...states]
  .map((s) => ({ state: s.state, rate: s.rates[9].crude }))
  .sort((a, b) => b.rate - a.rate)
  .slice(0, 10);
dashboard.getRange("K4:L14").values = [["State", "2024 rate"], ...top10.map((d) => [d.state, d.rate])];
dashboard.getRange("K4:L4").format = { fill: gold, font: { color: navy, bold: true } };
dashboard.getRange("L5:L14").format.numberFormat = "0.00";
const topChart = dashboard.charts.add("bar", dashboard.getRange("K4:L14"));
topChart.title = "Highest state rates in 2024";
topChart.hasLegend = false;
topChart.xAxis = { axisType: "textAxis" };
topChart.yAxis = { numberFormatCode: "0.0", min: 0, title: { text: "Deaths per 100,000" } };
topChart.setPosition("H16", "N34");
dashboard.getRange("A27:G30").merge();
dashboard.getRange("A27").values = [[
  "Interpret carefully: statewide totals cannot establish claims about offender race, victim–offender relationships, or neighborhood-level risk. Those require incident-level data and appropriate population denominators.",
]];
dashboard.getRange("A27:G30").format = {
  fill: "#FFF4DD",
  font: { color: ink, italic: true },
  wrapText: true,
  verticalAlignment: "center",
  borders: { preset: "outside", style: "thin", color: gold },
};
dashboard.getRange("A:N").format.font = { name: "Aptos", color: ink };
dashboard.getRange("A1:N2").format.font = { name: "Aptos", color: white, bold: true, size: 20 };
dashboard.getRange("A:A").format.columnWidth = 23;
dashboard.getRange("B:B").format.columnWidth = 12;
dashboard.getRange("C:C").format.columnWidth = 3;
dashboard.getRange("D:D").format.columnWidth = 23;
dashboard.getRange("E:E").format.columnWidth = 12;
dashboard.getRange("F:G").format.columnWidth = 10;
dashboard.getRange("H:H").format.columnWidth = 12;
dashboard.getRange("I:I").format.columnWidth = 15;
dashboard.getRange("J:J").format.columnWidth = 3;
dashboard.getRange("K:K").format.columnWidth = 20;
dashboard.getRange("L:L").format.columnWidth = 12;
dashboard.getRange("M:N").format.columnWidth = 10;

// State matrix
titleBand(
  ratesSheet,
  "A1:P2",
  "State homicide mortality rates",
  "Crude rates per 100,000 residents; use the Long Data sheet for both crude and age-adjusted values.",
);
ratesSheet.getRange("A5:P5").values = [[
  "State", ...years, "Δ rate", "% change", "Peak", "Peak year", "2024 rank",
]];
ratesSheet.getRange("A6:K55").values = states.map((s) => [s.state, ...s.rates.map((d) => d.crude)]);
for (let row = 6; row <= 55; row++) {
  ratesSheet.getRange(`L${row}:P${row}`).formulas = [[
    `=K${row}-B${row}`,
    `=IFERROR(K${row}/B${row}-1,0)`,
    `=MAX(B${row}:K${row})`,
    `=MATCH(N${row},B${row}:K${row},0)+2014`,
    `=RANK(K${row},$K$6:$K$55,0)`,
  ]];
}
ratesSheet.getRange("A5:P5").format = {
  fill: navy,
  font: { color: white, bold: true },
  wrapText: true,
  horizontalAlignment: "center",
};
ratesSheet.getRange("A6:A55").format = { fill: paleBlue, font: { bold: true, color: navy } };
ratesSheet.getRange("B6:N55").format.numberFormat = "0.00";
ratesSheet.getRange("M6:M55").format.numberFormat = "0.0%";
ratesSheet.getRange("O6:P55").format.numberFormat = "0";
ratesSheet.getRange("B6:K55").conditionalFormats.add("colorScale", {
  thresholds: ["min", "50%", "max"],
  colors: ["#E4F2EF", "#F9E7A5", "#D95D5D"],
});
ratesSheet.getRange("A5:P55").format.borders = { preset: "all", style: "thin", color: border };
ratesSheet.getRange("A5:P55").format.font = { name: "Aptos", size: 10 };
ratesSheet.getRange("A:A").format.columnWidth = 20;
ratesSheet.getRange("B:K").format.columnWidth = 9;
ratesSheet.getRange("L:P").format.columnWidth = 11;
ratesSheet.freezePanes.freezeRows(5);
ratesSheet.freezePanes.freezeColumns(1);
ratesSheet.tables.add("A5:P55", true, "StateRateMatrix");

// Long-form data
titleBand(
  longSheet,
  "A1:E2",
  "Long-form state data (500 observations)",
  "One row per state-year. Crude rate is the requested per-capita measure; age-adjusted rate is included for comparison.",
);
const longRows = [];
for (const s of states) {
  s.rates.forEach((rate, i) => longRows.push([`US-${s.fips}`, s.state, years[i], rate.crude, rate.adjusted]));
}
longSheet.getRange("A5:E505").values = [
  ["State FIPS", "State", "Year", "Crude rate", "Age-adjusted rate"],
  ...longRows,
];
longSheet.getRange("A5:E5").format = { fill: navy, font: { color: white, bold: true } };
longSheet.getRange("D6:E505").format.numberFormat = "0.00";
longSheet.getRange("A5:E505").format.borders = { preset: "all", style: "thin", color: border };
longSheet.getRange("A:A").format.columnWidth = 11;
longSheet.getRange("B:B").format.columnWidth = 20;
longSheet.getRange("C:C").format.columnWidth = 10;
longSheet.getRange("D:E").format.columnWidth = 19;
longSheet.freezePanes.freezeRows(5);
longSheet.tables.add("A5:E505", true, "LongStateRates");

// National series
titleBand(
  nationalSheet,
  "A1:F2",
  "National homicide mortality trend",
  "United States, all ages, all sexes, all races and ethnicities; D.C. is included in the national total.",
);
nationalSheet.getRange("A5:F15").values = [
  ["Year", "Deaths", "Population", "Crude rate", "Age-adjusted rate", "YoY crude change"],
  ...national.map((d) => [d.year, d.deaths, d.population, d.crude, d.adjusted, null]),
];
nationalSheet.getRange("F7:F15").formulas = national.slice(1).map((_, i) => [`=D${i + 7}/D${i + 6}-1`]);
nationalSheet.getRange("A5:F5").format = { fill: navy, font: { color: white, bold: true } };
nationalSheet.getRange("B6:C15").format.numberFormat = "#,##0";
nationalSheet.getRange("D6:E15").format.numberFormat = "0.00";
nationalSheet.getRange("F6:F15").format.numberFormat = "0.0%";
nationalSheet.getRange("A5:F15").format.borders = { preset: "all", style: "thin", color: border };
nationalSheet.getRange("A:F").format.columnWidth = 18;
nationalSheet.tables.add("A5:F15", true, "NationalTrend");
nationalSheet.freezePanes.freezeRows(5);

// Notes and sources
titleBand(
  notes,
  "A1:B2",
  "Definitions, limitations, and sources",
  "Read this sheet before comparing these figures with police-reported “murder” statistics or social-media claims.",
);
const noteRows = [
  ["Measure", "Homicide mortality: deaths in which the underlying cause is homicide, by the decedent’s state of residence."],
  ["Rate", "Crude death rate per 100,000 residents. This is the direct per-capita measure requested."],
  ["Age-adjusted rate", "Standardized to the 2000 U.S. population; useful when comparing states with different age structures."],
  ["Cause codes", "ICD-10 X85–Y09, Y87.1, *U01–*U02. Legal intervention is excluded."],
  ["Period", "Final annual data for 2015–2024, the latest ten complete years available in WISQARS when retrieved July 28, 2026."],
  ["Geography", "All 50 states are included; District of Columbia and territories are excluded from state tables. National values include D.C."],
  ["Small numbers", "CDC marks rates based on fewer than 20 deaths as unstable. Several small-state rates fluctuate sharply year to year."],
  ["What this does not show", "These statewide totals do not identify offenders, victim–offender relationships, race, firearm involvement, or neighborhood concentration."],
  ["Murder vs. homicide mortality", "Police “murder and nonnegligent manslaughter” counts and death-certificate homicide counts are related but not identical measures."],
  ["Primary source", "CDC WISQARS Fatal Injury Reports — https://wisqars.cdc.gov/reports/"],
  ["Data description", "CDC WISQARS Fatal Injury Data — https://wisqars.cdc.gov/about/fatal-injury-data"],
  ["Source system", "CDC/NCHS National Vital Statistics System (NVSS) annual mortality files."],
  ["Retrieved", "July 28, 2026"],
];
notes.getRange("A5:B17").values = noteRows;
notes.getRange("A5:A17").format = { fill: paleBlue, font: { color: navy, bold: true }, verticalAlignment: "top" };
notes.getRange("B5:B17").format = { wrapText: true, verticalAlignment: "top" };
notes.getRange("A5:B17").format.borders = { preset: "all", style: "thin", color: border };
notes.getRange("A:A").format.columnWidth = 24;
notes.getRange("B:B").format.columnWidth = 95;
notes.getRange("5:17").format.rowHeight = 36;
notes.freezePanes.freezeRows(4);

const inspect = await wb.inspect({
  kind: "workbook,sheet,table,formula",
  maxChars: 12000,
  tableMaxRows: 8,
  tableMaxCols: 8,
  options: { maxResults: 100 },
});
await fs.writeFile(path.join(outputDir, "inspection.txt"), inspect.ndjson ?? String(inspect), "utf8");

const previews = [
  ["Dashboard", "A1:N34", "preview_dashboard.png"],
  ["State Rates", "A1:P20", "preview_state_rates.png"],
  ["Long Data", "A1:E30", "preview_long_data.png"],
  ["National", "A1:F15", "preview_national.png"],
  ["Notes & Sources", "A1:B17", "preview_notes.png"],
];
for (const [sheetName, range, fileName] of previews) {
  const blob = await wb.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(outputDir, fileName), new Uint8Array(await blob.arrayBuffer()));
}

const out = await SpreadsheetFile.exportXlsx(wb);
await out.save(path.join(outputDir, "state_homicide_rates_2015_2024.xlsx"));
console.log(JSON.stringify({
  output: path.join(outputDir, "state_homicide_rates_2015_2024.xlsx"),
  states: states.length,
  observations: longRows.length,
  nationalPeak: national.reduce((a, b) => a.crude > b.crude ? a : b),
}, null, 2));
