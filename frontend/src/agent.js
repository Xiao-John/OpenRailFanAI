// App actions are explicit and local. Model/file text never executes as code.
export function themeCommand(message) {
  const text = String(message).replace(/[\s。！!，,]+/g, "").toLowerCase();
  if (/不要|别|不想|如果|假如|怎么|如何|什么|为什么|[“”"「」]/.test(text)) return null;
  if (/^(请|帮我|给我)?撤销(刚才的?|上次的?)?(主题|外观)(切换|修改|设置)?$/.test(text)) return "undo";
  const match = text.match(/^(?:请|帮我|给我)?(?:把(?:主题|外观)?|将(?:主题|外观)?)?(?:(?:主题|外观)(?:设置为|设为|改成|改为|切换到)?|切换到|切换成|换成|改成|改为|开启|打开|用|设置为|设为)?(深色|浅色|暗色|亮色|黑夜|夜间|白天|跟随系统)(?:主题|模式)?$/);
  if (!match) return null;
  const mode = match[1];
  return mode === "跟随系统" ? "auto" : ["深色", "暗色", "黑夜", "夜间"].includes(mode) ? "dark" : "light";
}

export function fileCommand(text) {
  return /导出|生成.{0,12}(文件|日历|日程|表格|csv|ics)|整理.{0,12}(表格|文件)|读取.{0,8}文件/i.test(text);
}

export function fileHistory(messages, upto = messages.length) {
  return messages.slice(0, upto)
    .filter(m => ["user", "assistant"].includes(m.role) && !m.meta?.error && !m.meta?.stopped)
    .slice(-20)
    .map(m => ({ role: m.role, content: [m.content || "",
      m.role === "assistant" && m.meta?.displayResults?.length
        ? "[已展示的结构化结果]\n" + JSON.stringify(m.meta.displayResults) : "",
      m.role === "assistant" && m.meta?.sources?.length
        ? "[来源]\n" + JSON.stringify(m.meta.sources) : "",
    ].filter(Boolean).join("\n\n") }));
}

export function inputTypes(entry) {
  return Array.isArray(entry?.input_types) && entry.input_types.includes("image") ? ["text", "image"] : ["text"];
}

export async function pickTextFile(imagesOnly = false) {
  return new Promise((resolve, reject) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = imagesOnly ? ".png,.jpg,.jpeg,.webp,.gif" : ".txt,.md,.tex,.csv,.json,.doc,.docx,.rtf,.png,.jpg,.jpeg,.webp,.gif";
    input.onchange = () => {
      const file = input.files?.[0];
      if (!file) return resolve(null);
      if (file.size > 8 * 1024 * 1024) return reject(new Error("附件不能超过 8 MiB"));
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("读取文件失败"));
      reader.onload = () => resolve({ name: file.name, content_base64: String(reader.result).split(",")[1],
        ...(file.type.startsWith("image/") ? {mime: file.type} : {}) });
      reader.readAsDataURL(file);
    };
    input.oncancel = () => resolve(null);
    input.click();
  });
}

export function downloadArtifact(file) {
  if (!/^railfan-draft\.(csv|md|json|ics)$/.test(file.name)) throw new Error("不支持的文件名");
  const bytes = Uint8Array.from(atob(file.content_base64), c => c.charCodeAt(0));
  if (bytes.length > 1024 * 1024) throw new Error("文件过大");
  const url = URL.createObjectURL(new Blob([bytes], { type: file.mime }));
  const link = document.createElement("a"); link.href = url; link.download = file.name;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 60000);
}
