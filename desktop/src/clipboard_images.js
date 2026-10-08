export function clipboardImageFiles(clipboardData) {
  const files = Array.from(clipboardData.files).filter((file) => file.type.toLowerCase().startsWith("image/"));
  if (files.length > 0) return files;

  return Array.from(clipboardData.items)
    .filter((item) => item.kind === "file" && item.type.toLowerCase().startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter((file) => file !== null);
}
