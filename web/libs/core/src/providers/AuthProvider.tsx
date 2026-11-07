import { createContext, memo, useCallback, useContext, useMemo } from "react";
import type { APIUser } from "../types/user";
import { useAtomValue } from "jotai";
import { queryClientAtom } from "jotai-tanstack-query";
import { currentUserAtom, currentUserUpdateAtom } from "../atoms/user";

export enum ABILITY {
  // Organization
  can_create_organizations = "organizations.create",
  can_view_organizations = "organizations.view",
  can_change_organizations = "organizations.change",
  can_delete_organizations = "organizations.delete",
  can_invite_organization = "organizations.invite",

  // Projects
  can_create_projects = "projects.create",
  can_view_projects = "projects.view",
  can_change_projects = "projects.change",
  can_delete_projects = "projects.delete",
  can_reset_project_cache = "projects.reset_cache",

  // Tasks
  can_create_tasks = "tasks.create",
  can_view_tasks = "tasks.view",
  can_change_tasks = "tasks.change",
  can_delete_tasks = "tasks.delete",

  // Annotations
  can_create_annotations = "annotations.create",
  can_view_annotations = "annotations.view",
  can_change_annotations = "annotations.change",
  can_delete_annotations = "annotations.delete",
  // School-specific: college-wide annotation data page (college admins / system admin)
  can_view_all_annotations = "annotations.view_all",

  // Labels
  can_create_labels = "labels.create",
  can_view_labels = "labels.view",
  can_change_labels = "labels.change",
  can_delete_labels = "labels.delete",

  // Actions (task assignment, bulk actions)
  can_perform_actions = "actions.perform",

  // Predictions
  can_manage_predictions = "predictions.any",

  // Token
  can_create_tokens = "users.token.any",

  // Avatar
  can_manage_avatar = "avatar.any",

  // Models
  can_create_models = "models.create",
  can_view_models = "models.view",
  can_change_models = "models.change",
  can_delete_models = "models.delete",

  // Model Provider Connections
  can_create_model_provider = "model_provider_connection.create",
  can_view_model_provider = "model_provider_connection.view",
  can_change_model_provider = "model_provider_connection.change",
  can_delete_model_provider = "model_provider_connection.delete",

  // Webhooks
  can_view_webhooks = "webhooks.view",
  can_change_webhooks = "webhooks.change",

  // DM Views
  can_reset_dm_views = "views.reset",

  // Storage
  can_view_storage = "storages.view",
  can_manage_storage = "storages.change",
  can_sync_storage = "storages.sync",

}

export type Ability = ABILITY;

export type AuthPermissions = {
  can: (a: string) => boolean;
  canAny: (as: string[]) => boolean;
  canAll: (as: string[]) => boolean;
};

type AuthState = {
  user: APIUser | null;
  isLoading: boolean;
  refetch: () => void;
  update: (userUpdate: Partial<APIUser>) => Promise<APIUser | undefined>;
  permissions: AuthPermissions;
};

const AuthContext = createContext<AuthState | null>(null);

const makePermissionChecker = (list?: (Ability | string)[]) => {
  const abilities = new Set<string>((list as string[]) ?? []);
  const has = (a: string) => {
    if (abilities.size === 0) return false;
    if (abilities.has(`-${a}`)) return false;
    if (abilities.has("*")) return true;
    return abilities.has(a);
  };
  return {
    can: (a: string) => has(a),
    canAny: (arr: string[]) => arr.some((a) => !abilities.has(`-${a}`) && (abilities.has("*") || abilities.has(a))),
    canAll: (arr: string[]) => arr.every((a) => !abilities.has(`-${a}`) && (abilities.has("*") || abilities.has(a))),
  };
};

export const AuthProvider = memo<{ children: React.ReactNode }>(({ children }) => {
  // User query and mutation
  const userQuery = useAtomValue(currentUserAtom);
  const updateUserMutation = useAtomValue(currentUserUpdateAtom);
  const queryClient = useAtomValue(queryClientAtom);

  // User functions
  const refetch = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["current-user"] });
  }, [queryClient]);

  const update = useCallback(
    async (userUpdate: Partial<APIUser>) => {
      if (!userQuery.data) {
        console.error("User is not loaded. Try fetching first.");
        return undefined;
      }
      return await updateUserMutation.mutateAsync({ pk: userQuery.data.id, user: userUpdate });
    },
    [userQuery.data, updateUserMutation],
  );

  // Permissions
  const checker = useMemo(() => makePermissionChecker(userQuery.data?.permissions), [userQuery.data?.permissions]);
  const permissionHelpers = useMemo<AuthPermissions>(() => {
    return {
      can: (a: string) => checker.can(String(a)),
      canAny: (abilities: string[]) => checker.canAny(abilities.map((a) => String(a))),
      canAll: (abilities: string[]) => checker.canAll(abilities.map((a) => String(a))),
    };
  }, [checker]);

  const contextValue: AuthState = useMemo(() => {
    return {
      user: userQuery.isSuccess ? userQuery.data : null,
      isLoading: userQuery.isFetching || updateUserMutation.isPending,
      refetch,
      update,
      permissions: permissionHelpers,
    };
  }, [
    userQuery.isSuccess,
    userQuery.data,
    userQuery.isFetching,
    updateUserMutation.isPending,
    refetch,
    update,
    permissionHelpers,
  ]);

  return <AuthContext.Provider value={contextValue}>{children}</AuthContext.Provider>;
});

export const useAuth = () => {
  const ctx = useContext(AuthContext)!;

  return {
    user: ctx.user,
    isLoading: ctx?.isLoading ?? false,
    refetch: ctx.refetch,
    update: ctx.update,
    permissions: ctx.permissions,
  };
};
