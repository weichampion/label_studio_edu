import { canManageProject, hasProjectFeature } from "./project-permissions";

describe("project feature permissions", () => {
  it("preserves community behavior when no school grant is returned", () => {
    expect(hasProjectFeature({}, "export_data")).toBe(true);
    expect(canManageProject({})).toBe(true);
    expect(hasProjectFeature({ school_features: null }, "annotate")).toBe(true);
  });

  it("defaults assigned students to annotation only", () => {
    const project = { school_features: ["annotate"], school_can_manage: false };
    expect(hasProjectFeature(project, "annotate")).toBe(true);
    expect(hasProjectFeature(project, "import_tasks")).toBe(false);
    expect(hasProjectFeature(project, "export_data")).toBe(false);
    expect(hasProjectFeature(project, "configure_project")).toBe(false);
    expect(canManageProject(project)).toBe(false);
  });

  it("supports read-only projects and project-specific feature combinations", () => {
    expect(hasProjectFeature({ school_features: [] }, "annotate")).toBe(false);
    expect(hasProjectFeature({ school_features: ["export_data"] }, "export_data")).toBe(true);
    expect(hasProjectFeature({ school_features: ["export_data"] }, "annotate")).toBe(false);
    expect(canManageProject({ school_features: ["configure_project"], school_can_manage: false })).toBe(false);
  });
});
