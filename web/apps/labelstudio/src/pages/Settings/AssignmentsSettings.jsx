import { Button, EmptyState, SimpleCard, Spinner, Typography } from "@humansignal/ui";
import { ABILITY, useAuth } from "@humansignal/core/providers/AuthProvider";
import { createTitleFromSegments, useUpdatePageTitle } from "@humansignal/core";
import { IconGrid, IconInfo } from "@humansignal/icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import { confirm } from "../../components/Modal/Modal";
import { useAPI } from "../../providers/ApiProvider";
import { useProject } from "../../providers/ProjectProvider";
import { cn } from "../../utils/bem";

const STATUS_META = {
  assigned: { label: "Assigned", bg: "#f3f4f6", text: "#374151" },
  in_progress: { label: "In progress", bg: "#dbeafe", text: "#1e40af" },
  completed: { label: "Completed", bg: "#d1fae5", text: "#065f46" },
};

const TYPE_META = {
  AN: { label: "Annotator", bg: "#fef3c7", text: "#92400e" },
  RE: { label: "Reviewer", bg: "#f3e8ff", text: "#6b21a8" },
};

const StatusBadge = ({ status, type }) => {
  const meta =
    status === "completed" || status === "in_progress" || status === "assigned"
      ? STATUS_META[status]
      : STATUS_META.assigned;
  const typeMeta = TYPE_META[type] ?? TYPE_META.AN;

  return (
    <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
      <span
        style={{
          background: typeMeta.bg,
          color: typeMeta.text,
          borderRadius: 4,
          padding: "1px 6px",
          fontSize: 12,
        }}
      >
        {typeMeta.label}
      </span>
      <span
        style={{
          background: meta.bg,
          color: meta.text,
          borderRadius: 4,
          padding: "1px 6px",
          fontSize: 12,
        }}
      >
        {meta.label}
      </span>
    </span>
  );
};

export const AssignmentsSettings = () => {
  const { project } = useProject();
  const api = useAPI();
  const { permissions } = useAuth();
  const canView = permissions.can(ABILITY.can_view_assignments);
  const canDelete = permissions.can(ABILITY.can_delete_assignments);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  useUpdatePageTitle(createTitleFromSegments([project?.title, "Assignments"]));

  const fetchAssignments = useCallback(async () => {
    if (!project?.id) return;
    setLoading(true);
    const res = await api.callApi("projectAssignments", { params: { pk: project.id } });
    setRows(Array.isArray(res) ? res : (res?.results ?? []));
    setLoading(false);
  }, [api, project?.id]);

  useEffect(() => {
    fetchAssignments();
  }, [fetchAssignments]);

  const groups = useMemo(() => {
    const byUser = new Map();
    for (const row of rows) {
      const key = row.user;
      if (!byUser.has(key)) {
        byUser.set(key, { user: key, user_email: row.user_email, rows: [] });
      }
      byUser.get(key).rows.push(row);
    }
    return [...byUser.values()].sort((a, b) => (a.user_email ?? "").localeCompare(b.user_email ?? ""));
  }, [rows]);

  const onDelete = useCallback(
    (assignment) => {
      confirm({
        title: "Delete assignment",
        body: `Remove task #${assignment.task} (${TYPE_META[assignment.type]?.label ?? assignment.type}) from ${assignment.user_email}? This action cannot be undone.`,
        buttonLook: "negative",
        onOk: async () => {
          const res = await api.callApi("deleteAssignment", { params: { pk: assignment.id } });
          if (res !== null) fetchAssignments();
        },
      });
    },
    [api, fetchAssignments],
  );

  if (!canView) {
    return (
      <EmptyState
        size="medium"
        variant="primary"
        icon={<IconInfo />}
        title="No access"
        description="You don't have permission to view task assignments."
      />
    );
  }

  if (loading) {
    return <Spinner size={32} style={{ margin: "auto" }} />;
  }

  if (rows.length === 0) {
    return (
      <EmptyState
        size="medium"
        variant="primary"
        icon={<IconGrid />}
        title="No assignments yet"
        description="Switch this project to manual assignment mode, then assign tasks to annotators and reviewers from the Data Manager."
      />
    );
  }

  return (
    <div className={cn("assignments-settings").toClassName()}>
      <div className={cn("assignments-settings").elem("wrapper").toClassName()}>
        <h1>Task Assignments</h1>
        <div className="space-y-4">
          {groups.map((group) => {
            const counts = { assigned: 0, in_progress: 0, completed: 0 };
            for (const row of group.rows) {
              counts[row.status] = (counts[row.status] ?? 0) + 1;
            }
            return (
              <SimpleCard key={group.user} title={group.user_email}>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
                  {["assigned", "in_progress", "completed"].map((status) => (
                    <span
                      key={status}
                      style={{
                        background: STATUS_META[status].bg,
                        color: STATUS_META[status].text,
                        borderRadius: 4,
                        padding: "2px 8px",
                        fontSize: 12,
                      }}
                    >
                      {STATUS_META[status].label}: {counts[status]}
                    </span>
                  ))}
                  <span
                    style={{
                      background: "#f3f4f6",
                      color: "#374151",
                      borderRadius: 4,
                      padding: "2px 8px",
                      fontSize: 12,
                    }}
                  >
                    Total: {group.rows.length}
                  </span>
                </div>
                <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
                  {group.rows.map((row) => (
                    <li
                      key={row.id}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        padding: "8px 0",
                        borderTop: "1px solid rgba(0,0,0,0.08)",
                      }}
                    >
                      <Typography size="medium">Task #{row.task}</Typography>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 12 }}>
                        <StatusBadge status={row.status} type={row.type} />
                        {canDelete && (
                          <Button size="small" variant="negative" look="outlined" onClick={() => onDelete(row)}>
                            Delete
                          </Button>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </SimpleCard>
            );
          })}
        </div>
      </div>
    </div>
  );
};

AssignmentsSettings.menuItem = "Assignments";
AssignmentsSettings.path = "/assignments";
AssignmentsSettings.exact = true;
