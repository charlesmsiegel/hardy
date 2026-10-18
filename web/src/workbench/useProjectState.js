// Action choices and inspection share the same exact project-state read.
// A response for a previous project is never a current catalog; refreshing
// keeps the same project's choices while usePanel protects request ownership.
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

export default function useProjectState() {
  const {status, revision} = useSession();
  const project = status.path;
  const panel = usePanel(project ? `/api/project-state?project=${encodeURIComponent(project)}` : null, revision, project);
  const state = panel.data?.project === project ? panel.data : {};
  return {
    project, state, catalog: state.catalog || {}, error: panel.error, reload: panel.reload,
    disabled: status.turn_running || status.command_running || status.provider_active,
  };
}
