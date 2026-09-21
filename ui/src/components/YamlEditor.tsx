import { useCallback } from "react";
import Editor from "@monaco-editor/react";
import type { OnMount } from "@monaco-editor/react";
import { cn } from "@/lib/utils";

interface YamlEditorProps {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  height?: string;
  className?: string;
}

export function YamlEditor({
  value,
  onChange,
  readOnly = false,
  height = "500px",
  className,
}: YamlEditorProps) {
  const handleMount: OnMount = useCallback(
    (editor) => {
      editor.updateOptions({
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        lineNumbers: "on",
        renderWhitespace: "boundary",
        tabSize: 2,
        insertSpaces: true,
        wordWrap: "on",
      });
    },
    []
  );

  return (
    <div className={cn("border border-border rounded-md overflow-hidden", className)}>
      <Editor
        height={height}
        language="yaml"
        value={value}
        onChange={(v) => onChange?.(v ?? "")}
        onMount={handleMount}
        loading={
          <div className="flex items-center justify-center h-full text-text-muted text-sm">
            加载编辑器...
          </div>
        }
        options={{
          readOnly,
          fontSize: 13,
          fontFamily:
            "'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', monospace",
          theme: "vs-dark",
          automaticLayout: true,
          folding: true,
          suggestOnTriggerCharacters: true,
          quickSuggestions: false,
          scrollbar: {
            vertical: "visible",
            horizontal: "visible",
          },
        }}
      />
    </div>
  );
}
