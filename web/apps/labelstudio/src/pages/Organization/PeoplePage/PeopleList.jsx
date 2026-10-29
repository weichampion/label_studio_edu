import { formatDistance } from "date-fns";
import { useCallback, useEffect, useState } from "react";
import { Userpic, useToast } from "@humansignal/ui";
import { Pagination, Spinner } from "../../../components";
import { usePage, usePageSize } from "../../../components/Pagination/Pagination";
import { useAPI } from "../../../providers/ApiProvider";
import { cn } from "../../../utils/bem";
import { isDefined } from "../../../utils/helpers";
import "./PeopleList.prefix.css";
import { CopyableTooltip } from "../../../components/CopyableTooltip/CopyableTooltip";
import { ABILITY, useAuth } from "@humansignal/core/providers/AuthProvider";

const ROLES = [
  { value: "annotator", label: "Annotator" },
  { value: "reviewer", label: "Reviewer" },
  { value: "manager", label: "Manager" },
  { value: "admin", label: "Admin" },
  { value: "owner", label: "Owner" },
];

const RoleLabel = ({ role }) => {
  const colors = {
    owner: { bg: "#f3e8ff", text: "#6b21a8" },
    admin: { bg: "#dbeafe", text: "#1e40af" },
    manager: { bg: "#d1fae5", text: "#065f46" },
    reviewer: { bg: "#fef3c7", text: "#92400e" },
    annotator: { bg: "#f3f4f6", text: "#374151" },
  };
  const c = colors[role] ?? colors.annotator;

  return (
    <span
      style={{
        backgroundColor: c.bg,
        color: c.text,
        padding: "2px 10px",
        borderRadius: 9999,
        fontSize: 12,
        fontWeight: 500,
        textTransform: "capitalize",
      }}
    >
      {role}
    </span>
  );
};

export const PeopleList = ({ onSelect, selectedUser, defaultSelected }) => {
  const api = useAPI();
  const toast = useToast();
  const { permissions } = useAuth();
  const canChangeRoles = permissions.can(ABILITY.can_change_organizations);
  const [usersList, setUsersList] = useState();
  const [currentPage] = usePage("page", 1);
  const [currentPageSize] = usePageSize("page_size", 30);
  const [totalItems, setTotalItems] = useState(0);

  const fetchUsers = useCallback(async (page, pageSize) => {
    const response = await api.callApi("memberships", {
      params: {
        pk: 1,
        contributed_to_projects: 1,
        page,
        page_size: pageSize,
      },
    });

    if (response.results) {
      setUsersList(response.results);
      setTotalItems(response.count);
    }
  }, []);

  const selectUser = useCallback(
    (user) => {
      if (selectedUser?.id === user.id) {
        onSelect?.(null);
      } else {
        onSelect?.(user);
      }
    },
    [selectedUser],
  );

  const handleRoleChange = useCallback(
    async (userPk, newRole) => {
      const result = await api.callApi("updateMemberRole", {
        params: { pk: 1, userPk },
        body: { role: newRole },
      });
      if (result) {
        toast.show({ message: "Role updated successfully" });
        fetchUsers(currentPage, currentPageSize);
      }
    },
    [api, fetchUsers, currentPage, currentPageSize, toast],
  );

  useEffect(() => {
    fetchUsers(currentPage, currentPageSize);
  }, []);

  useEffect(() => {
    if (isDefined(defaultSelected) && usersList) {
      const selected = usersList.find(({ user }) => user.id === Number(defaultSelected));

      if (selected) selectUser(selected.user);
    }
  }, [usersList, defaultSelected]);

  const handleRoleSelect = useCallback(
    (e, userPk) => {
      e.stopPropagation();
      handleRoleChange(userPk, e.target.value);
    },
    [handleRoleChange],
  );

  return (
    <>
      <div className={cn("people-list").toClassName()}>
        <div className={cn("people-list").elem("wrapper").toClassName()}>
          {usersList ? (
            <div className={cn("people-list").elem("users").toClassName()}>
              <div className={cn("people-list").elem("header").toClassName()}>
                <div className={cn("people-list").elem("column").mix("avatar").toClassName()} />
                <div className={cn("people-list").elem("column").mix("email").toClassName()}>Email</div>
                <div className={cn("people-list").elem("column").mix("name").toClassName()}>Name</div>
                <div className={cn("people-list").elem("column").mix("role").toClassName()}>Role</div>
                <div className={cn("people-list").elem("column").mix("last-activity").toClassName()}>Last Activity</div>
              </div>
              <div className={cn("people-list").elem("body").toClassName()}>
                {usersList.map((membership) => {
                  const { user, role } = membership;
                  const active = user.id === selectedUser?.id;

                  return (
                    <div
                      key={`user-${user.id}`}
                      className={cn("people-list").elem("user").mod({ active }).toClassName()}
                      onClick={() => selectUser(user)}
                    >
                      <div className={cn("people-list").elem("field").mix("avatar").toClassName()}>
                        <CopyableTooltip title={`User ID: ${user.id}`} textForCopy={user.id}>
                          <Userpic user={user} style={{ width: 28, height: 28 }} />
                        </CopyableTooltip>
                      </div>
                      <div className={cn("people-list").elem("field").mix("email").toClassName()}>{user.email}</div>
                      <div className={cn("people-list").elem("field").mix("name").toClassName()}>
                        {user.first_name} {user.last_name}
                      </div>
                      <div
                        className={cn("people-list").elem("field").mix("role").toClassName()}
                        onClick={(e) => e.stopPropagation()}
                      >
                        {canChangeRoles ? (
                          <select
                            value={role ?? "annotator"}
                            onChange={(e) => handleRoleSelect(e, user.id)}
                            style={{
                              padding: "4px 8px",
                              borderRadius: 6,
                              border: "1px solid #d1d5db",
                              fontSize: 13,
                              background: "#fff",
                              cursor: "pointer",
                            }}
                          >
                            {ROLES.map((r) => (
                              <option key={r.value} value={r.value}>
                                {r.label}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <RoleLabel role={role ?? "annotator"} />
                        )}
                      </div>
                      <div className={cn("people-list").elem("field").mix("last-activity").toClassName()}>
                        {formatDistance(new Date(user.last_activity), new Date(), { addSuffix: true })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className={cn("people-list").elem("loading").toClassName()}>
              <Spinner size={36} />
            </div>
          )}
        </div>
        <Pagination
          page={currentPage}
          urlParamName="page"
          totalItems={totalItems}
          pageSize={currentPageSize}
          pageSizeOptions={[30, 50, 100]}
          onPageLoad={fetchUsers}
          style={{ paddingTop: 16 }}
        />
      </div>
    </>
  );
};
