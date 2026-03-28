#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  codex-headless-collab.sh --mode <plan|apply|review> --file <path> [--workdir <dir>] [--outdir <dir>] [--model <model>] -- <task>

Examples:
  codex-headless-collab.sh --mode plan --file src/app.ts -- "Propose the safest fix and list tests."
  codex-headless-collab.sh --mode apply --file src/app.ts -- "Implement the agreed fix in this file only."
  codex-headless-collab.sh --mode review --file src/app.ts -- "Review this file for correctness and regressions."
EOF
}

mode=""
target=""
workdir=""
outdir=""
model=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      mode="${2:-}"
      shift 2
      ;;
    --file)
      target="${2:-}"
      shift 2
      ;;
    --workdir)
      workdir="${2:-}"
      shift 2
      ;;
    --outdir)
      outdir="${2:-}"
      shift 2
      ;;
    --model)
      model="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

task="${*:-}"
if [[ -z "$task" ]] && [[ ! -t 0 ]]; then
  task="$(cat)"
fi

if [[ -z "$mode" || -z "$target" || -z "$task" ]]; then
  usage >&2
  exit 2
fi

case "$mode" in
  plan|apply|review)
    ;;
  *)
    echo "Unsupported mode: $mode" >&2
    exit 2
    ;;
esac

codex_bin="$(command -v codex || true)"
if [[ -z "$codex_bin" ]]; then
  echo "ERROR: codex not found in PATH" >&2
  exit 127
fi

if [[ "$target" = /* ]]; then
  target_abs="$target"
else
  target_abs="$(cd "$(dirname "$target")" && pwd)/$(basename "$target")"
fi

if [[ ! -f "$target_abs" ]]; then
  echo "ERROR: target file not found: $target_abs" >&2
  exit 1
fi

if [[ -z "$workdir" ]]; then
  if git -C "$(dirname "$target_abs")" rev-parse --show-toplevel >/dev/null 2>&1; then
    workdir="$(git -C "$(dirname "$target_abs")" rev-parse --show-toplevel)"
  else
    workdir="$(dirname "$target_abs")"
  fi
fi

if [[ ! -d "$workdir" ]]; then
  echo "ERROR: workdir not found: $workdir" >&2
  exit 1
fi

if [[ -z "$outdir" ]]; then
  outdir="$workdir/.claude/tmp/codex-collab"
fi

mkdir -p "$outdir" "$outdir/locks"

lock_hash="$(printf '%s' "$target_abs" | shasum -a 256 | awk '{print $1}')"
lock_dir="$outdir/locks/$lock_hash"

if ! mkdir "$lock_dir" 2>/dev/null; then
  echo "ERROR: another Codex collaboration run already holds this target: $target_abs" >&2
  exit 1
fi

cleanup() {
  rmdir "$lock_dir" 2>/dev/null || true
}

trap cleanup EXIT

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="$outdir/$stamp-$mode"
mkdir -p "$run_dir"

prompt_file="$run_dir/prompt.txt"
log_file="$run_dir/codex.log"
last_message_file="$run_dir/last-message.txt"
before_file="$run_dir/target.before"
after_file="$run_dir/target.after"
before_changed_file="$run_dir/changed-before.txt"
after_changed_file="$run_dir/changed-after.txt"
changed_files_file="$run_dir/changed-files.txt"

if [[ "$target_abs" = "$workdir"/* ]]; then
  target_rel="${target_abs#$workdir/}"
else
  target_rel="$target_abs"
fi

cp "$target_abs" "$before_file"

git_repo="false"
if git -C "$workdir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git_repo="true"
  git -C "$workdir" diff --name-only >"$before_changed_file" || true
  if ! git -C "$workdir" diff --quiet -- "$target_rel"; then
    echo "WARNING: target file already has uncommitted changes: $target_rel" >&2
  fi
fi

case "$mode" in
  plan)
    sandbox="read-only"
    mode_instructions="Return a concrete plan, key risks, and the smallest safe diff shape. Do not modify files."
    ;;
  apply)
    sandbox="workspace-write"
    mode_instructions="Implement the change directly in the target file if it can be completed within that file only. Keep the diff minimal. If another file is required, do not touch it; explain the blocker in the final message."
    ;;
  review)
    sandbox="read-only"
    mode_instructions="Review the current contents of the target file. Focus on bugs, regressions, edge cases, and missing tests. Do not modify files."
    ;;
esac

cat >"$prompt_file" <<EOF
You are Codex collaborating with Claude Code on a bounded coding task.

Mode: $mode
Repository root: $workdir
Target file: $target_abs

Task:
$task

Rules:
- Claude Code is the orchestrator and final integrator.
- Read surrounding repo context if needed, but only modify the target file.
- If another file is necessary, do not change it. Explain exactly what file and why.
- Keep changes production-oriented and minimal.
- End with assumptions, verification, and residual risks.

Mode instructions:
$mode_instructions
EOF

cmd=(
  "$codex_bin"
  exec
  -C "$workdir"
  --skip-git-repo-check
  --color never
  --json
  -o "$last_message_file"
)

if [[ -n "$model" ]]; then
  cmd+=(-m "$model")
fi

case "$sandbox" in
  read-only)
    cmd+=(-s read-only)
    ;;
  workspace-write)
    cmd+=(--full-auto)
    ;;
esac

cmd+=(-)

set +e
"${cmd[@]}" <"$prompt_file" >"$log_file" 2>&1
status=$?
set -e

cp "$target_abs" "$after_file"

if [[ "$git_repo" == "true" ]]; then
  git -C "$workdir" diff --name-only >"$after_changed_file" || true
  sort -u "$before_changed_file" >"$run_dir/changed-before.sorted"
  sort -u "$after_changed_file" >"$run_dir/changed-after.sorted"
  comm -13 "$run_dir/changed-before.sorted" "$run_dir/changed-after.sorted" >"$changed_files_file" || true
  if ! cmp -s "$before_file" "$after_file"; then
    printf '%s\n' "$target_rel" >>"$changed_files_file"
  fi
  sort -u "$changed_files_file" -o "$changed_files_file"
  git -C "$workdir" diff -- "$target_rel" >"$run_dir/target.diff" || true
else
  : >"$changed_files_file"
  if ! cmp -s "$before_file" "$after_file"; then
    printf '%s\n' "$target_abs" >>"$changed_files_file"
  fi
fi

printf 'STATUS=%s\n' "$status"
printf 'RUN_DIR=%s\n' "$run_dir"
printf 'PROMPT_FILE=%s\n' "$prompt_file"
printf 'LOG_FILE=%s\n' "$log_file"
printf 'LAST_MESSAGE=%s\n' "$last_message_file"
printf 'CHANGED_FILES=%s\n' "$changed_files_file"

if [[ -s "$changed_files_file" ]]; then
  echo '--- CHANGED FILES ---'
  cat "$changed_files_file"
fi

if [[ -f "$last_message_file" ]]; then
  echo '--- LAST MESSAGE ---'
  cat "$last_message_file"
fi

if [[ $status -ne 0 ]]; then
  echo '--- CODEX LOG TAIL ---' >&2
  tail -n 80 "$log_file" >&2 || true
fi

exit $status
