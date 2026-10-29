import { ProjectsPage } from "./Projects/Projects";
import { HomePage } from "./Home/HomePage";
import { OrganizationPage } from "./Organization";
import { WorkspacesPage } from "./Workspaces/WorkspacesPage";
import { ModelsPage } from "./Organization/Models/ModelsPage";
import { FF_HOMEPAGE, isFF } from "../utils/feature-flags";
import { pages } from "@humansignal/app-common";

export const Pages = [
  isFF(FF_HOMEPAGE) && HomePage,
  ProjectsPage,
  OrganizationPage,
  WorkspacesPage,
  ModelsPage,
  pages.AccountSettingsPage,
].filter(Boolean);
