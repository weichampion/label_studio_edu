import { SidebarMenu } from "../../components/SidebarMenu/SidebarMenu";
import { WorkspaceList } from "./WorkspaceList";
import { WorkspaceDetail } from "./WorkspaceDetail";

const MenuLayout = ({ children, ...routeProps }) => {
  const menuItems = [
    { ...WorkspaceList, title: "All Workspaces", menuItem: "All Workspaces", path: "/" },
  ];

  return <SidebarMenu menuItems={menuItems} path={routeProps.match.url} children={children} />;
};

const pages = {
  WorkspaceDetail,
};

export const WorkspacesPage = {
  title: "Workspaces",
  path: "/workspaces",
  exact: true,
  layout: MenuLayout,
  component: WorkspaceList,
  pages,
};
