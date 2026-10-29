import { Button, Typography, useToast } from "@humansignal/ui";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ABILITY, useAuth } from "@humansignal/core/providers/AuthProvider";
import { useUpdatePageTitle } from "@humansignal/core";
import { HeidiTips } from "../../../components/HeidiTips/HeidiTips";
import { Modal, info, modal } from "../../../components/Modal/Modal";
import { Input } from "../../../components/Form";
import { Space } from "../../../components/Space/Space";
import { useAPI } from "../../../providers/ApiProvider";
import { cn } from "../../../utils/bem";
import { FF_AUTH_TOKENS, isFF } from "../../../utils/feature-flags";
import "./PeopleInvitation.prefix.css";
import { PeopleList } from "./PeopleList";
import "./PeoplePage.prefix.css";
import { TokenSettingsModal } from "@humansignal/app-common/blocks/TokenSettingsModal";
import { IconPlus } from "@humansignal/icons";
import { InviteLink } from "./InviteLink";
import { SelectedUser } from "./SelectedUser";

const USER_ROLES = [
  { value: "manager", label: "Manager (Teacher)" },
  { value: "reviewer", label: "Reviewer (TA)" },
  { value: "annotator", label: "Annotator (Student)" },
  { value: "admin", label: "Administrator" },
];

const CreateUserModal = ({ onClose, onCreated }) => {
  const api = useAPI();
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("manager");
  const [workspace, setWorkspace] = useState("");
  const [workspaces, setWorkspaces] = useState([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.callApi("workspaces", { params: { page_size: 999 } }).then((res) => {
      if (res) setWorkspaces(res.results ?? res);
    });
  }, [api]);

  const onCreate = useCallback(async () => {
    if (!email.trim() || creating) return;
    setCreating(true);
    setError(null);
    const res = await api.callApi("createUserAccount", {
      body: {
        email: email.trim(),
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        password: password,
        role,
        workspace: workspace ? Number(workspace) : null,
      },
      errorFilter: () => true,
    });
    setCreating(false);
    if (!res?.$meta?.ok) {
      const data = res?.response;
      const pick = (v) => (Array.isArray(v) ? v[0] : v);
      setError(pick(data?.non_field_errors) ?? pick(data?.detail) ?? pick(data?.email) ?? "Failed to create user");
      return;
    }
    toast.show({ message: `User ${res.email} created` });
    if (res.initial_password) {
      info({
        title: "Account created",
        body: (
          <div style={{ lineHeight: 1.9 }}>
            <div>
              Email: <b>{res.email}</b>
            </div>
            <div>
              Initial password:{" "}
              <code style={{ padding: "2px 8px", background: "#f3f4f6", borderRadius: 4 }}>{res.initial_password}</code>
            </div>
            <Typography size="small" className="text-neutral-content-subtler">
              Share these credentials with the user. They can change the password after logging in.
            </Typography>
          </div>
        ),
      });
    }
    onCreated(res);
  }, [api, toast, email, firstName, lastName, password, role, workspace, creating, onCreated]);

  const fieldSelectStyle = {
    width: "100%",
    padding: "8px 12px",
    borderRadius: 6,
    border: "1px solid #d1d5db",
    fontSize: 14,
    background: "#fff",
  };

  return (
    <Modal
      animateAppearance
      title="Create User"
      style={{ width: 480 }}
      onHide={onClose}
      bareFooter
      footer={
        <Space spread>
          <Button look="outlined" variant="neutral" onClick={onClose}>
            Cancel
          </Button>
          <Button look="primary" onClick={onCreate} waiting={creating} disabled={!email.trim()}>
            Create
          </Button>
        </Space>
      }
    >
      <div style={{ padding: "16px 0", display: "flex", flexDirection: "column", gap: 16 }}>
        <Input name="email" label="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <Input name="first_name" label="First name" value={firstName} onChange={(e) => setFirstName(e.target.value)} />
        <Input name="last_name" label="Last name" value={lastName} onChange={(e) => setLastName(e.target.value)} />
        <Input
          name="password"
          type="password"
          label="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Leave blank to auto-generate"
        />
        <div>
          <label style={{ display: "block", marginBottom: 8, fontWeight: 500 }}>Organization Role</label>
          <select value={role} onChange={(e) => setRole(e.target.value)} style={fieldSelectStyle}>
            {USER_ROLES.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label style={{ display: "block", marginBottom: 8, fontWeight: 500 }}>Workspace (optional)</label>
          <select value={workspace} onChange={(e) => setWorkspace(e.target.value)} style={fieldSelectStyle}>
            <option value="">None</option>
            {workspaces.map((ws) => (
              <option key={ws.id} value={ws.id}>
                {ws.title}
              </option>
            ))}
          </select>
        </div>
        {error && (
          <Typography size="small" style={{ color: "#dc2626" }}>
            {error}
          </Typography>
        )}
      </div>
    </Modal>
  );
};

export const PeoplePage = () => {
  const toast = useToast();
  const { permissions } = useAuth();
  const apiSettingsModal = useRef();
  const [selectedUser, setSelectedUser] = useState(null);
  const [invitationOpen, setInvitationOpen] = useState(false);
  const [showCreateUser, setShowCreateUser] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useUpdatePageTitle("People");

  const selectUser = useCallback(
    (user) => {
      setSelectedUser(user);

      localStorage.setItem("selectedUser", user?.id);
    },
    [setSelectedUser],
  );

  const apiTokensSettingsModalProps = useMemo(
    () => ({
      title: "API Token Settings",
      style: { width: 480 },
      body: () => (
        <TokenSettingsModal
          onSaved={() => {
            toast.show({ message: "API Token settings saved" });
            apiSettingsModal.current?.close();
          }}
        />
      ),
    }),
    [],
  );

  const showApiTokenSettingsModal = useCallback(() => {
    apiSettingsModal.current = modal(apiTokensSettingsModalProps);
    __lsa("organization.token_settings");
  }, [apiTokensSettingsModalProps]);

  const defaultSelected = useMemo(() => {
    return localStorage.getItem("selectedUser");
  }, []);

  return (
    <div className={cn("people").toClassName()}>
      <div className={cn("people").elem("controls").toClassName()}>
        <Space spread>
          <Space />

          <Space>
            {isFF(FF_AUTH_TOKENS) && (
              <Button look="outlined" onClick={showApiTokenSettingsModal} aria-label="Show API token settings">
                API Tokens Settings
              </Button>
            )}
            {permissions.can(ABILITY.can_change_organizations) && (
              <Button
                leading={<IconPlus className="!h-4" />}
                onClick={() => setShowCreateUser(true)}
                aria-label="Create user"
              >
                Create User
              </Button>
            )}
            <Button
              leading={<IconPlus className="!h-4" />}
              onClick={() => setInvitationOpen(true)}
              aria-label="Invite new member"
            >
              Add Members
            </Button>
          </Space>
        </Space>
      </div>
      <div className={cn("people").elem("content").toClassName()}>
        <PeopleList
          key={reloadKey}
          selectedUser={selectedUser}
          defaultSelected={defaultSelected}
          onSelect={(user) => selectUser(user)}
        />

        {selectedUser ? (
          <SelectedUser user={selectedUser} onClose={() => selectUser(null)} />
        ) : (
          <HeidiTips collection="organizationPage" />
        )}
      </div>
      <InviteLink
        opened={invitationOpen}
        onClosed={() => {
          console.log("hidden");
          setInvitationOpen(false);
        }}
      />
      {showCreateUser && (
        <CreateUserModal
          onClose={() => setShowCreateUser(false)}
          onCreated={() => {
            setShowCreateUser(false);
            setReloadKey((k) => k + 1);
          }}
        />
      )}
    </div>
  );
};

PeoplePage.title = "People";
PeoplePage.path = "/";
