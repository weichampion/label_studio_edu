import { Button, Typography, Select as SelectUI, Userpic } from "@humansignal/ui";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useHistory } from "react-router-dom";
import { IconPlus, IconCross } from "@humansignal/icons";
import { Modal } from "../../components/Modal/Modal";
import { Spinner, Pagination } from "../../components";
import { usePage, usePageSize } from "../../components/Pagination/Pagination";
import { useAPI } from "../../providers/ApiProvider";
import { cn } from "../../utils/bem";
import { Input } from "../../components/Form";
import { Space } from "../../components/Space/Space";

export const WorkspaceList = () => {
  const api = useAPI();
  const history = useHistory();
  const [workspaces, setWorkspaces] = useState(null);
  const [currentPage] = usePage("page", 1);
  const [currentPageSize] = usePageSize("page_size", 30);
  const [totalItems, setTotalItems] = useState(0);
  const [showCreate, setShowCreate] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [creating, setCreating] = useState(false);

  const fetchWorkspaces = useCallback(async (page, pageSize) => {
    const response = await api.callApi("workspaces", {
      params: { page, page_size: pageSize },
    });

    if (response) {
      setWorkspaces(response.results ?? response);
      setTotalItems(response.count ?? 0);
    }
  }, [api]);

  useEffect(() => {
    fetchWorkspaces(currentPage, currentPageSize);
  }, [fetchWorkspaces, currentPage, currentPageSize]);

  const handleCreate = useCallback(async () => {
    if (!newTitle.trim()) return;
    setCreating(true);
    const result = await api.callApi("createWorkspace", {
      body: { title: newTitle.trim() },
    });
    setCreating(false);
    if (result) {
      setShowCreate(false);
      setNewTitle("");
      fetchWorkspaces(currentPage, currentPageSize);
    }
  }, [newTitle, api, fetchWorkspaces, currentPage, currentPageSize]);

  return (
    <div className={cn("workspaces-page").toClassName()}>
      <div className={cn("workspaces-page").elem("controls").toClassName()}>
        <Space spread>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>Workspaces</h1>
          <Button
            leading={<IconPlus className="!h-4" />}
            look="primary"
            onClick={() => setShowCreate(true)}
            aria-label="Create workspace"
          >
            New Workspace
          </Button>
        </Space>
      </div>

      <div className={cn("workspaces-page").elem("content").toClassName()}>
        {workspaces ? (
          workspaces.length === 0 ? (
            <div style={{ textAlign: "center", padding: 60 }}>
              <Typography size="large" className="text-neutral-content-subtler">
                No workspaces yet. Create one to organize projects into departments or classes.
              </Typography>
            </div>
          ) : (
            <div className={cn("workspace-list").toClassName()}>
              <div className={cn("workspace-list").elem("header").toClassName()}>
                <div className={cn("workspace-list").elem("column").mix("title").toClassName()}>Name</div>
                <div className={cn("workspace-list").elem("column").mix("members").toClassName()}>Members</div>
                <div className={cn("workspace-list").elem("column").mix("projects").toClassName()}>Projects</div>
                <div className={cn("workspace-list").elem("column").mix("created").toClassName()}>Created</div>
              </div>
              <div className={cn("workspace-list").elem("body").toClassName()}>
                {workspaces.map((ws) => (
                  <div
                    key={ws.id}
                    className={cn("workspace-list").elem("row").toClassName()}
                    onClick={() => history.push(`/workspaces/${ws.id}`)}
                    style={{ cursor: "pointer" }}
                  >
                    <div className={cn("workspace-list").elem("field").mix("title").toClassName()}>
                      <Typography>{ws.title}</Typography>
                      {ws.is_archived && (
                        <span className={cn("workspace-list").elem("archived-badge").toClassName()}>(archived)</span>
                      )}
                    </div>
                    <div className={cn("workspace-list").elem("field").mix("members").toClassName()}>
                      {ws.member_count ?? "—"}
                    </div>
                    <div className={cn("workspace-list").elem("field").mix("projects").toClassName()}>
                      {ws.project_count ?? "—"}
                    </div>
                    <div className={cn("workspace-list").elem("field").mix("created").toClassName()}>
                      {ws.created_at ? new Date(ws.created_at).toLocaleDateString() : "—"}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )
        ) : (
          <div style={{ textAlign: "center", padding: 60 }}>
            <Spinner size={36} />
          </div>
        )}
        <Pagination
          page={currentPage}
          urlParamName="page"
          totalItems={totalItems}
          pageSize={currentPageSize}
          pageSizeOptions={[30, 50, 100]}
          onPageLoad={fetchWorkspaces}
          style={{ paddingTop: 16 }}
        />
      </div>

      {showCreate && (
        <Modal
          animateAppearance
          title="Create Workspace"
          style={{ width: 480 }}
          onHide={() => { setShowCreate(false); setNewTitle(""); }}
          bareFooter
          footer={
            <Space spread>
              <Button
                look="outlined"
                variant="neutral"
                onClick={() => { setShowCreate(false); setNewTitle(""); }}
              >
                Cancel
              </Button>
              <Button
                look="primary"
                onClick={handleCreate}
                waiting={creating}
                disabled={!newTitle.trim()}
              >
                Create
              </Button>
            </Space>
          }
        >
          <div style={{ padding: "16px 0" }}>
            <label style={{ display: "block", marginBottom: 8, fontWeight: 500 }}>
              Workspace Name
            </label>
            <Input
              name="title"
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="e.g. Computer Science Department"
              style={{ width: "100%" }}
              onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
            />
            <Typography size="small" className="text-neutral-content-subtler" style={{ marginTop: 8 }}>
              Workspaces help organize projects by department, class, or team.
            </Typography>
          </div>
        </Modal>
      )}
    </div>
  );
};

WorkspaceList.title = "Workspaces";
WorkspaceList.path = "/";
WorkspaceList.exact = true;
