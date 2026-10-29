type SchoolProject = {
  school_features?: string[] | null;
  school_can_manage?: boolean | null;
};

export const hasProjectFeature = (project: SchoolProject | undefined, feature: string): boolean =>
  !Array.isArray(project?.school_features) || project.school_features.includes(feature);

export const canManageProject = (project: SchoolProject | undefined): boolean =>
  project?.school_can_manage !== false;
