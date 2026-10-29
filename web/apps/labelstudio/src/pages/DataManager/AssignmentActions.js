/**
 * Registers assignment actions on the DataManager instance.
 * Call this after the DM is initialized when project.assignment_mode === 'manual'.
 * `members` is the candidate list (project workspace members, or org members when
 * the project has no workspace).
 */
export function registerAssignmentActions(dataManager, api, project, members) {
  if (!dataManager?.addAction) return;

  const userOptions = (members ?? [])
    .filter((m) => m && m.id)
    .map((m) => ({ label: m.name || m.email || String(m.id), value: String(m.id) }));

  const action = {
    id: "assign-tasks",
    title: "Assign Tasks",
    order: 50,
    dialog: {
      title: "Assign Tasks",
      text: "Select users to assign the selected tasks to.",
      type: "prompt",
      form: [
        {
          columnCount: 1,
          fields: [
            {
              label: "Users",
              name: "user_ids",
              type: "select",
              options: userOptions,
              searchable: true,
            },
            {
              label: "Role",
              name: "type",
              type: "select",
              options: [
                { label: "Annotator", value: "AN" },
                { label: "Reviewer", value: "RE" },
              ],
              value: "AN",
            },
          ],
        },
      ],
    },
  };

  dataManager.addAction(action, async (actionParams) => {
    const selected = actionParams?.selectedItems;
    const taskIds = selected?.all ? [] : (selected?.included ?? []);
    const formData = actionParams?.formData ?? {};
    const userIds = Array.isArray(formData.user_ids)
      ? formData.user_ids.map(Number)
      : [Number(formData.user_ids)].filter(Boolean);

    if (!userIds.length) return;

    await api.callApi("createAssignment", {
      params: { pk: project.id },
      body: {
        task_ids: taskIds.length > 0 ? taskIds : undefined,
        user_ids: userIds,
        type: formData.type || "AN",
      },
    });

    return { success: true };
  });

  // Auto-distribute action
  const autoAction = {
    id: "auto-assign-tasks",
    title: "Auto-Assign Tasks",
    order: 51,
    dialog: {
      title: "Auto-Assign Tasks",
      text: "Automatically distribute selected tasks among chosen users (round-robin).",
      type: "prompt",
      form: [
        {
          columnCount: 1,
          fields: [
            {
              label: "Users",
              name: "user_ids",
              type: "select",
              options: userOptions,
              searchable: true,
            },
            {
              label: "Overlap",
              name: "overlap",
              type: "number",
              value: "1",
              placeholder: "1",
            },
            {
              label: "Role",
              name: "type",
              type: "select",
              options: [
                { label: "Annotator", value: "AN" },
                { label: "Reviewer", value: "RE" },
              ],
              value: "AN",
            },
          ],
        },
      ],
    },
  };

  dataManager.addAction(autoAction, async (actionParams) => {
    const selected = actionParams?.selectedItems;
    const taskIds = selected?.all ? [] : (selected?.included ?? []);
    const formData = actionParams?.formData ?? {};
    const userIds = Array.isArray(formData.user_ids)
      ? formData.user_ids.map(Number)
      : [Number(formData.user_ids)].filter(Boolean);
    const overlap = Math.max(1, Number.parseInt(formData.overlap || "1", 10) || 1);

    if (!userIds.length) return;

    await api.callApi("autoAssign", {
      params: { pk: project.id },
      body: {
        task_ids: taskIds.length > 0 ? taskIds : undefined,
        user_ids: userIds,
        overlap,
        type: formData.type || "AN",
      },
    });

    return { success: true };
  });
}
