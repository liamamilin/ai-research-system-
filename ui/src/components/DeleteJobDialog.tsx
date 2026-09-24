import { useState } from "react";
import { Trash2, TriangleAlert } from "lucide-react";
import { Modal } from "@/components/Modal";
import { errorMessage, useToast } from "@/lib/toast";
import { deleteJob } from "@/api";

interface Props {
  jobName: string | null;
  /** Blocked while the job is running; the backend refuses with 409 anyway. */
  isRunning?: boolean;
  /** Files the job depends on, e.g. pipeline stages. */
  warnings?: string[];
  onDeleted?: (jobName: string, backupPath: string) => void;
  onClose: () => void;
}

/**
 * One confirmation flow for job deletion, shared by the list and the detail
 * page. The file is backed up server-side first, so the dialog can say where it
 * went instead of claiming the action is irreversible.
 */
export function DeleteJobDialog({ jobName, isRunning, warnings = [], onDeleted, onClose }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const toast = useToast();

  if (!jobName) return null;

  const confirm = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await deleteJob(jobName);
      onDeleted?.(jobName, result?.backup_path || "");
      toast.success(
        `已删除 ${jobName}`,
        result?.backup_path ? `备份：${result.backup_path}` : undefined,
      );
    } catch (err: unknown) {
      setError(errorMessage(err, "删除失败"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={!!jobName} onClose={onClose} title={`删除 ${jobName}`} className="w-full max-w-md">
      <div className="p-5 space-y-3">
        <p className="text-sm text-text-muted">
          将删除该 job 的 YAML 文件，它会从 Jobs 列表和周期调度中消失。
        </p>

        <div className="text-xs text-text-muted/80 font-mono break-all bg-bg-hover rounded px-2 py-1.5">
          jobs/{jobName}.yaml
        </div>

        <p className="text-xs text-text-muted flex items-start gap-1.5">
          <Trash2 className="w-3.5 h-3.5 mt-px shrink-0" />
          删除前会自动备份到 <span className="font-mono">state/backups/</span>，可随时恢复。
        </p>

        {warnings.map((w) => (
          <div key={w} className="flex items-start gap-1.5 text-xs text-warning">
            <TriangleAlert className="w-3.5 h-3.5 mt-px shrink-0" />
            <span>{w}</span>
          </div>
        ))}

        {isRunning && (
          <div className="text-xs text-danger">该 job 正在运行，请先停止后再删除。</div>
        )}

        {error && (
          <div role="alert" className="text-xs text-danger bg-red-900/20 px-2 py-1.5 rounded">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <button className="btn text-xs" onClick={onClose} disabled={busy}>取消</button>
          <button className="btn btn-danger text-xs" onClick={confirm} disabled={busy || isRunning}>
            {busy ? "删除中..." : "删除"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
