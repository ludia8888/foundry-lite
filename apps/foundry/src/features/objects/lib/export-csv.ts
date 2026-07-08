import type { GenericObject } from "@foundry-lite/sdk";
import type { FoundryLiteOntologyObjectView } from "@foundry-lite/sdk/react";

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text =
    typeof value === "object" ? JSON.stringify(value) : String(value);
  return `"${text.replaceAll('"', '""')}"`;
}

/** 현재 결과 페이지를 CSV로 만들어 클라이언트에서 다운로드한다 (BOM 포함, Excel 한글 호환). */
export function downloadObjectsCsv(
  objects: readonly GenericObject[],
  objectView: FoundryLiteOntologyObjectView,
): void {
  const columns = objectView.properties.map((property) => property.apiName);
  const header = ["objectId", ...columns].map(csvCell).join(",");
  const rows = objects.map((object) =>
    [object.objectId, ...columns.map((column) => object.properties[column])]
      .map(csvCell)
      .join(","),
  );
  const BOM = "\uFEFF";
  const csv = `${BOM}${[header, ...rows].join("\n")}`;
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${objectView.apiName}-결과.csv`;
  anchor.click();
  URL.revokeObjectURL(url);
}
