import { SidebarMenu } from "../../components/SidebarMenu/SidebarMenu";
import { WebhookPage } from "../WebhookPage/WebhookPage";
import { DangerZone } from "./DangerZone";
import { GeneralSettings } from "./GeneralSettings";
import { AnnotationSettings } from "./AnnotationSettings";
import { LabelingSettings } from "./LabelingSettings";
import { MachineLearningSettings } from "./MachineLearningSettings/MachineLearningSettings";
import { PredictionsSettings } from "./PredictionsSettings/PredictionsSettings";
import { StorageSettings } from "./StorageSettings/StorageSettings";
import "./settings.prefix.css";
import { useLocation, Redirect } from "react-router-dom";
import { useProject } from "../../providers/ProjectProvider";
import { canManageProject, hasProjectFeature } from "@humansignal/core/lib/utils/project-permissions";

export const MenuLayout = ({ children, ...routeProps }) => {
  const { project } = useProject();
  const location = useLocation();
  const canConfigure = hasProjectFeature(project, "configure_project");
  const canDelete = hasProjectFeature(project, "delete_project");
  const manager = canManageProject(project);
  const allowedPages = [
    ...(canConfigure ? [GeneralSettings, LabelingSettings, AnnotationSettings] : []),
    ...(manager ? [MachineLearningSettings, PredictionsSettings, StorageSettings, WebhookPage] : []),
    ...(canDelete || manager ? [DangerZone] : []),
  ];
  if (project.id && !manager) {
    const section = location.pathname.slice(location.pathname.indexOf("/settings") + "/settings".length);
    if ((!canConfigure && !canDelete) || ["/ml", "/predictions", "/storage", "/webhooks"].some(
      (path) => section.startsWith(path)
    )) {
      return <Redirect to={`/projects/${project.id}/data`} />;
    }
    if (!canConfigure && !section.startsWith("/danger-zone")) {
      return <Redirect to={`/projects/${project.id}/settings/danger-zone`} />;
    }
    if (!canDelete && section.startsWith("/danger-zone")) {
      return <Redirect to={`/projects/${project.id}/data`} />;
    }
  }
  return (
    <SidebarMenu
      menuItems={allowedPages.filter(Boolean)}
      path={routeProps.match.url}
      children={children}
    />
  );
};

const pages = {
  AnnotationSettings,
  LabelingSettings,
  MachineLearningSettings,
  PredictionsSettings,
  StorageSettings,
  WebhookPage,
  DangerZone,
};

export const SettingsPage = {
  title: "Settings",
  path: "/settings",
  exact: true,
  layout: MenuLayout,
  component: GeneralSettings,
  pages,
};
