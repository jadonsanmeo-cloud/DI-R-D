import { ChatContext } from '@/app/chat-context';
import ModelSelector from '@/components/chat/header/model-selector';
import type { ChartConfig, ChartType } from '@/new-components/charts/types';
import type {
  ExecutionStep as ManusExecutionStep,
  StepType,
  ThinkingSection,
} from '@/new-components/chat/content/ManusLeftPanel';
import type {
  ActiveStepInfo,
  ExecutionOutput as ManusExecutionOutput,
  PanelView,
} from '@/new-components/chat/content/ManusRightPanel';
import SpecConfirmationCard from '@/new-components/chat/content/SpecConfirmationCard';
import TaskPlanCard, { TaskItem } from '@/new-components/chat/content/TaskPlanCard';
import { AttachedConnector, ConnectorInstance } from '@/new-components/connector/types';
import MethodHubToggle from '@/new-components/responses-chat/MethodHubToggle';
import FromTaskBanner from '@/new-components/scheduled-task/FromTaskBanner';
import SaveAsScheduledTaskDrawer from '@/new-components/scheduled-task/SaveAsScheduledTaskDrawer';
import type { EditableExecutionSpec, ResponseConfirmationState } from '@/types/responses';
import type { ChatReplayPayload } from '@/types/scheduled-task';
import axios from '@/utils/ctx-axios';
import { sendSpacePostRequest } from '@/utils/request';
import {
  getResponseHistory,
  getResponseHistorySessionId,
  notifyResponseHistoryChanged,
} from '@/utils/responses-history';
import { fetchRuntimeCapabilities, initialMethodHubEnabled } from '@/utils/runtime-capabilities';
import ApiOutlined from '@ant-design/icons/ApiOutlined';
import ArrowUpOutlined from '@ant-design/icons/ArrowUpOutlined';
import BookOutlined from '@ant-design/icons/BookOutlined';
import CheckCircleFilled from '@ant-design/icons/CheckCircleFilled';
import CloudServerOutlined from '@ant-design/icons/CloudServerOutlined';
import ConsoleSqlOutlined from '@ant-design/icons/ConsoleSqlOutlined';
import DatabaseOutlined from '@ant-design/icons/DatabaseOutlined';
import FileExcelOutlined from '@ant-design/icons/FileExcelOutlined';
import FileImageOutlined from '@ant-design/icons/FileImageOutlined';
import FileOutlined from '@ant-design/icons/FileOutlined';
import FilePptOutlined from '@ant-design/icons/FilePptOutlined';
import FileTextOutlined from '@ant-design/icons/FileTextOutlined';
import LeftOutlined from '@ant-design/icons/LeftOutlined';
import PaperClipOutlined from '@ant-design/icons/PaperClipOutlined';
import PlusOutlined from '@ant-design/icons/PlusOutlined';
import ReadOutlined from '@ant-design/icons/ReadOutlined';
import RightOutlined from '@ant-design/icons/RightOutlined';
import UploadOutlined from '@ant-design/icons/UploadOutlined';
import { useRequest } from 'ahooks';
import { Button, ConfigProvider, Dropdown, Input, List, Modal, Spin, Tag, Tooltip, message } from 'antd';
import type { NextPage } from 'next';
import dynamic from 'next/dynamic';
import Image from 'next/image';
import { useRouter } from 'next/router';
import type { ChangeEvent } from 'react';
import { useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

const ManusLeftPanel = dynamic(() => import('@/new-components/chat/content/ManusLeftPanel'), {
  ssr: false,
  loading: () => <Spin size='small' />,
});

const ManusRightPanel = dynamic(() => import('@/new-components/chat/content/ManusRightPanel'), {
  ssr: false,
  loading: () => <Spin size='small' />,
});

const generateUUID = () => {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
};

const PENDING_RESPONSE_STORAGE_KEY = 'data-intelligence.pending-response';

const cleanFinalContent = (text: string): string => {
  let cleaned = text.replace(/\\n/g, '\n').trim();
  cleaned = cleaned.replace(/\n{3,}/g, '\n\n');
  cleaned = cleaned.replace(/"\s*\}\s*$/, '').trim();
  // Strip raw ReAct prefixes that may leak from the backend
  cleaned = cleaned.replace(/^(Thought|Action|Action Input|Observation|Phase):\s*/gm, '').trim();
  return cleaned;
};

const _getFileIcon = (fileName: string, mimeType?: string) => {
  const ext = fileName.toLowerCase().split('.').pop() || '';
  if (
    ['xlsx', 'xls', 'csv'].includes(ext) ||
    mimeType?.includes('spreadsheet') ||
    mimeType?.includes('excel') ||
    mimeType?.includes('csv')
  ) {
    return <FileExcelOutlined className='text-green-600 text-lg' />;
  }
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext) || mimeType?.includes('image')) {
    return <FileImageOutlined className='text-pink-500 text-lg' />;
  }
  if (['ppt', 'pptx'].includes(ext)) {
    return <FilePptOutlined className='text-orange-500 text-lg' />;
  }
  return <FileTextOutlined className='text-blue-500 text-lg' />;
};

interface DataSource {
  id: number;
  type: string;
  params: Record<string, any>;
  description?: string;
  db_name: string; // derived from params.name
  db_type: string; // alias for type
  gmt_created?: string;
  gmt_modified?: string;
}

// Define Knowledge Base Interface (Partial)
interface KnowledgeSpace {
  id: number;
  name: string;
  vector_type: string;
  desc?: string;
  owner?: string;
}

// Define file attachment type for user messages
interface FileAttachment {
  name: string;
  size: number;
  type: string;
  count?: number;
}

// Define message type for chat
interface ChatMessage {
  id?: string;
  role: 'human' | 'view';
  context: string;
  model_name?: string;
  order?: number;
  thinking?: boolean;
  attachedFile?: FileAttachment;
  attachedKnowledge?: KnowledgeSpace;
  attachedSkill?: { name: string; id: string };
  attachedDb?: { db_name: string; db_type: string };
  taskPlan?: TaskItem[];
  attachedConnectors?: AttachedConnector[];
  confirmation?: ResponseConfirmationState;
  evidence?: unknown;
  responseMetadata?: Record<string, unknown>;
}

interface ExecutionStep {
  id: string;
  step: number;
  title?: string;
  detail: string;
  status: 'running' | 'done' | 'failed';
  action?: string;
  actionInput?: string;
  todoMeta?: {
    state?: 'init' | 'progress' | 'done';
    done?: number;
    total?: number;
  };
}

interface ExecutionOutput {
  output_type: string;
  content: any;
}

interface FilePreview {
  kind: 'table' | 'text';
  file_name?: string;
  file_path?: string;
  columns?: string[];
  rows?: Record<string, any>[];
  text?: string;
  shape?: [number, number];
}

interface ChartPreview {
  chartType?: ChartType;
  data: Array<{ x: string | number; y: number; [key: string]: any }>;
  xField: string;
  yField: string;
  seriesField?: string;
  colorField?: string;
  angleField?: string;
  title?: string;
  description?: string;
  smooth?: boolean;
}

interface Skill {
  id: string;
  name: string;
  description: string;
  type: 'official' | 'personal';
  icon?: string;
}

type ArtifactType = 'file' | 'table' | 'chart' | 'image' | 'code' | 'markdown' | 'summary' | 'html';

interface Artifact {
  id: string;
  type: ArtifactType;
  name: string;
  content: any;
  createdAt: number;
  messageId?: string;
  stepId?: string;
  downloadable?: boolean;
  mimeType?: string;
  size?: number;
  filePath?: string;
  // Chart-specific metadata
  chartType?: ChartType;
  chartConfig?: Partial<ChartConfig>;
}

// Convert execution data to Manus panel format
const convertToManusFormat = (
  execution:
    | {
        steps: ExecutionStep[];
        outputs: Record<string, ExecutionOutput[]>;
        activeStepId: string | null;
        collapsed: boolean;
        stepThoughts?: Record<string, string>;
      }
    | undefined,
  _userQuery?: string,
  t?: (key: string) => string,
): {
  sections: ThinkingSection[];
  activeStep: ActiveStepInfo | null;
  outputs: ManusExecutionOutput[];
  stepThoughts: Record<string, string>;
} => {
  if (!execution || !execution.steps.length) {
    return { sections: [], activeStep: null, outputs: [], stepThoughts: execution?.stepThoughts || {} };
  }

  // Determine step type from title
  const getStepType = (title?: string, action?: string): StepType => {
    // Check action name first — it's the most reliable indicator
    const actionLower = (action || '').toLowerCase();
    if (
      actionLower.includes('skill') ||
      actionLower === 'execute_skill_script_file' ||
      actionLower === 'get_skill_resource' ||
      actionLower === 'select_skill' ||
      actionLower === 'load_skill'
    )
      return 'skill';
    if (actionLower === 'shell_interpreter') return 'bash';
    if (actionLower === 'sql_query') return 'sql';

    const lower = (title || '').toLowerCase();
    if (
      lower.includes('load_skill') ||
      lower.includes('load skill') ||
      lower.includes('execute_skill_script_file') ||
      lower.includes('get_skill_resource') ||
      lower.includes('select_skill')
    )
      return 'skill';
    if (lower.includes('sql_query') || lower.includes('sql query') || lower.includes('sql\u67e5\u8be2')) return 'sql';
    if (lower.includes('read') || lower.includes('load')) return 'read';
    if (lower.includes('edit')) return 'edit';
    if (lower.includes('write') || lower.includes('save')) return 'write';
    if (lower.includes('bash') || lower.includes('execute') || lower.includes('command') || lower.includes('shell'))
      return 'bash';
    if (lower.includes('grep') || lower.includes('search')) return 'grep';
    if (lower.includes('glob') || lower.includes('find')) return 'glob';
    if (lower.includes('html')) return 'html';
    if (lower.includes('python') || lower.includes('code')) return 'python';
    if (lower.includes('skill')) return 'skill';
    if (lower.includes('task')) return 'task';
    return 'other';
  };

  // Get step status mapping
  const getStepStatus = (status: string): 'pending' | 'running' | 'completed' | 'error' => {
    if (status === 'running') return 'running';
    if (status === 'done') return 'completed';
    if (status === 'failed') return 'error';
    return 'pending';
  };

  // Group steps into sections (for now, create one section with all steps)
  // In a more advanced version, you could group by phase/category
  const steps: ManusExecutionStep[] = execution.steps
    .filter(step => {
      const detail = (step.detail || '').toLowerCase();
      return !detail.includes('action: terminate');
    })
    .map(step => {
      const cleanDetail = step.detail?.replace(/^Thought:.*\n?/gm, '').trim();
      return {
        id: step.id,
        type: getStepType(step.title, step.action),
        title: step.title || `Step ${step.step}`,
        subtitle: cleanDetail?.split('\n')[0]?.slice(0, 80),
        description: cleanDetail || undefined,
        phase: (step as any).phase,
        status: getStepStatus(step.status),
      };
    });

  const sections: ThinkingSection[] = [
    {
      id: 'section-execution',
      title: t ? t('execution_steps') : 'Execution Steps',
      isCompleted: steps.every(s => s.status === 'completed'),
      steps,
    },
  ];

  // Get active step info
  let activeStep: ActiveStepInfo | null = null;
  if (execution.activeStepId) {
    const step = execution.steps.find(s => s.id === execution.activeStepId);
    if (step) {
      const cleanDetail = step.detail?.replace(/^Thought:.*\n?/gm, '').trim();
      activeStep = {
        id: step.id,
        type: getStepType(step.title, step.action),
        title: step.title || `Step ${step.step}`,
        subtitle: cleanDetail?.split('\n')[0]?.slice(0, 80),
        status: getStepStatus(step.status),
        detail: cleanDetail,
        action: step.action,
        actionInput: step.actionInput,
      };
    }
  }

  // Get outputs for active step
  const outputs: ManusExecutionOutput[] = execution.activeStepId
    ? (execution.outputs[execution.activeStepId] || []).map(o => ({
        output_type: o.output_type as any,
        content: o.content,
        timestamp: Date.now(),
      }))
    : [];

  return { sections, activeStep, outputs, stepThoughts: execution?.stepThoughts || {} };
};

const EXAMPLE_CARDS = [
  {
    id: 'db_profile_report',
    icon: '🗄️',
    title: 'Database Profile & Analysis Report',
    description: 'Connect to a database, generate a database profile, and create a visual web report',
    query:
      'Please analyze the currently connected database, generate a database profile (including table structure, field information, and data volume statistics), and create a polished interactive web analysis report.',
    dbName: 'Walmart_Sales',
    color: 'from-emerald-500/10 to-teal-500/10',
    borderColor: 'border-emerald-200/60 dark:border-emerald-800/40',
    iconBg: 'bg-emerald-100 dark:bg-emerald-900/40',
  },
  {
    id: 'fin_report',
    icon: '📈',
    title: 'Financial Report In-depth Analysis',
    description: 'Analyze an annual report and generate a data visualization report',
    query:
      'Please deeply analyze this 2019 annual report, including revenue and profit trends, asset-liability structure, cash flow analysis, key financial indicators, and generate a professional interactive web analysis report.',
    fileName:
      '2020-01-23__\u6d59\u6c5f\u6d77\u7fd4\u836f\u4e1a\u80a1\u4efd\u6709\u9650\u516c\u53f8__002099__\u6d77\u7fd4\u836f\u4e1a__2019\u5e74__\u5e74\u5ea6\u62a5\u544a.pdf',
    fileType: 'application/pdf',
    fileSize: 2621440, // ~2.5 MB
    color: 'from-violet-500/10 to-purple-500/10',
    borderColor: 'border-violet-200/60 dark:border-violet-800/40',
    iconBg: 'bg-violet-100 dark:bg-violet-900/40',
    skillName: 'financial-report-analyzer',
  },
];

const Playground: NextPage = () => {
  const router = useRouter();
  const { t } = useTranslation();
  const { model, setModel } = useContext(ChatContext);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [methodHubEnabled, setMethodHubEnabled] = useState(false);
  const [methodHubAvailable, setMethodHubAvailable] = useState(false);
  const [runtimeCapabilitiesLoading, setRuntimeCapabilitiesLoading] = useState(true);

  // Selection State
  const [isDbModalOpen, setIsDbModalOpen] = useState(false);
  const [isKnowledgeModalOpen, setIsKnowledgeModalOpen] = useState(false);

  // Contexts
  const [selectedDb, setSelectedDb] = useState<DataSource | null>(null);
  const [selectedKnowledge, setSelectedKnowledge] = useState<KnowledgeSpace | null>(null);
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
  const uploadedFile = uploadedFiles[0] ?? null;
  const hasUploadedFiles = uploadedFiles.length > 0;

  // Chat messages state
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const [executionMap, setExecutionMap] = useState<
    Record<
      string,
      {
        steps: ExecutionStep[];
        outputs: Record<string, ExecutionOutput[]>;
        activeStepId: string | null;
        collapsed: boolean;
        stepThoughts: Record<string, string>;
      }
    >
  >({});
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null);
  const [uploadedFilePath, setUploadedFilePath] = useState<string | null>(null);
  const [backendQaUploadActive, setBackendQaUploadActive] = useState(false);
  const [filePreview, setFilePreview] = useState<FilePreview | null>(null);
  const [_filePreviewLoading, setFilePreviewLoading] = useState(false);
  const [_filePreviewError, setFilePreviewError] = useState<string | null>(null);
  const [chartPreview, setChartPreview] = useState<ChartPreview | null>(null);
  const lastArtifactKeyRef = useRef<string>('');

  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [createdSkillNames, setCreatedSkillNames] = useState<Record<string, string>>({});
  const [streamingSummary, setStreamingSummary] = useState<string>('');
  const [_summaryComplete, setSummaryComplete] = useState(false);

  const [isSkillPanelOpen, setIsSkillPanelOpen] = useState(false);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [skillSearchQuery, setSkillSearchQuery] = useState('');

  const [isKnowledgePanelOpen, setIsKnowledgePanelOpen] = useState(false);
  const [knowledgeSearchQuery, setKnowledgeSearchQuery] = useState('');

  const [isDbPanelOpen, setIsDbPanelOpen] = useState(false);
  const [dbSearchQuery, setDbSearchQuery] = useState('');

  const [isConnectorPanelOpen, setIsConnectorPanelOpen] = useState(false);
  const [selectedConnectors, setSelectedConnectors] = useState<ConnectorInstance[]>([]);
  const [connectorSearchQuery, setConnectorSearchQuery] = useState('');
  const [isScheduleOpen, setScheduleOpen] = useState(false);
  const connectorsList: ConnectorInstance[] = [];

  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState(false);
  const [rightPanelView, setRightPanelView] = useState<PanelView>('execution');
  const [previewArtifact, setPreviewArtifact] = useState<Artifact | null>(null);

  // Active round tracking: which view message is currently selected for the right panel
  const [activeViewMsgId, setActiveViewMsgId] = useState<string | null>(null);

  // Track step IDs that belong to a terminate action so we can suppress them
  const terminatedStepIdsRef = useRef<Set<string>>(new Set());
  const methodHubModeRestoredRef = useRef(false);
  const preloadedFilePathRef = useRef<string | null>(null);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  // Snapshot of the exact payload last sent to the agent, captured at send
  // time so "保存定时任务" can replay the real execution (file / database /
  // knowledge / skill / connectors) instead of a drifting UI state.
  const lastSentPayloadRef = useRef<ChatReplayPayload | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setRuntimeCapabilitiesLoading(true);
    fetchRuntimeCapabilities(controller.signal)
      .then(capabilities => {
        setMethodHubAvailable(capabilities.method_hub.available);
        if (!router.query.response_id && !methodHubModeRestoredRef.current) {
          setMethodHubEnabled(initialMethodHubEnabled(capabilities));
        }
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        setMethodHubAvailable(false);
        setMethodHubEnabled(false);
        message.error(error instanceof Error ? error.message : t('method_hub_unavailable'));
      })
      .finally(() => {
        if (!controller.signal.aborted) setRuntimeCapabilitiesLoading(false);
      });
    return () => controller.abort();
  }, [router.query.response_id, t]);

  useEffect(() => {
    const raw = sessionStorage.getItem(PENDING_RESPONSE_STORAGE_KEY);
    if (!raw) return;
    let pending: { messageId: string; responseId: string; token: string };
    try {
      pending = JSON.parse(raw);
    } catch {
      sessionStorage.removeItem(PENDING_RESPONSE_STORAGE_KEY);
      return;
    }
    fetch(`${process.env.API_BASE_URL ?? ''}/api/v1/responses/${pending.responseId}`, {
      headers: { 'X-Confirmation-Token': pending.token },
    })
      .then(async response => {
        if (!response.ok) throw new Error(`Recovery failed with status ${response.status}`);
        return response.json();
      })
      .then(payload => {
        if (payload.status !== 'awaiting_confirmation') {
          sessionStorage.removeItem(PENDING_RESPONSE_STORAGE_KEY);
          return;
        }
        if (typeof payload.runtime_options?.method_hub_enabled === 'boolean') {
          methodHubModeRestoredRef.current = true;
          setMethodHubEnabled(payload.runtime_options.method_hub_enabled);
        }
        const confirmation: ResponseConfirmationState = {
          responseId: pending.responseId,
          token: pending.token,
          revision: payload.revision,
          intent: payload.intent?.value || payload.spec?.intent || 'unknown',
          spec: payload.spec,
          expiresAt: payload.expires_at,
        };
        setMessages(current => {
          const existing = current.find(item => item.id === pending.messageId);
          if (existing) {
            return current.map(item =>
              item.id === pending.messageId ? { ...item, thinking: false, confirmation } : item,
            );
          }
          return [
            ...current,
            {
              id: pending.messageId,
              role: 'view',
              context: '',
              thinking: false,
              confirmation,
            },
          ];
        });
      })
      .catch(() => sessionStorage.removeItem(PENDING_RESPONSE_STORAGE_KEY));
  }, []);

  const [historyLoading, setHistoryLoading] = useState(false);
  const [contextStatus, setContextStatus] = useState<{
    state: 'OK' | 'WARNING' | 'ERROR';
    used_tokens: number;
    max_tokens: number;
    usage_percent: number;
    layer: string | null;
  } | null>(null);
  const [taskPlan, setTaskPlan] = useState<TaskItem[]>([]);

  // Fetch Data Sources lazily when the picker opens.
  const {
    data: dataSources,
    loading: _loadingSources,
    runAsync: fetchDataSources,
  } = useRequest(
    async () => {
      try {
        const response: any = await axios.get('/api/v2/serve/datasources');
        // ctx-axios interceptor returns response.data directly, so response is {success, data, ...}
        const result = response?.success !== undefined ? response : response?.data;
        if (result?.success) {
          return (result.data || []).map((item: any) => ({
            ...item,
            db_name: item.db_name || item.params?.name || item.params?.database || `${item.type}-${item.id}`,
            db_type: item.type,
          })) as DataSource[];
        }
        return [];
      } catch (e) {
        console.error('Failed to fetch datasources', e);
        return [];
      }
    },
    { manual: true },
  );

  // Fetch Knowledge Bases lazily when the picker opens.
  const {
    data: knowledgeSpaces,
    loading: _loadingKnowledge,
    runAsync: fetchKnowledgeSpaces,
  } = useRequest(
    async () => {
      try {
        const response = await sendSpacePostRequest('/knowledge/space/list', {});
        // ctx-axios interceptor returns response.data directly, so response is {success, data, ...}
        if (response?.success) {
          return response.data || [];
        }
        return [];
      } catch (e) {
        console.error('Failed to fetch knowledge spaces', e);
        return [];
      }
    },
    { manual: true },
  );

  const normalizeText = (value: unknown): string => {
    if (typeof value === 'string') return value;
    if (value && typeof value === 'object') {
      const todoValue = (value as Record<string, unknown>).TODO;
      if (typeof todoValue === 'string') return todoValue;
      try {
        return JSON.stringify(value);
      } catch {
        return String(value);
      }
    }
    return value == null ? '' : String(value);
  };

  /** Extract the actual created skill name from shell_interpreter output.
   *  Uses priority-based matching to avoid returning 'skill-creator' (the tool path). */
  const extractCreatedSkillName = (allText: string): string | null => {
    // Priority 1: Skill 'xxx' initialized/packaged (quoted name from output)
    const quotedSkill = allText.match(/[Ss]kill\s+['"]([\w-]+)['"]/);
    if (quotedSkill) return quotedSkill[1];

    // Priority 2: Initializing/Packaging skill: xxx
    const colonSkill = allText.match(/(?:Initializing|Packaging)\s+skill:\s*(?:skills\/)?([\w-]+)/);
    if (colonSkill) return colonSkill[1];

    // Priority 3: Created skill directory: .../skills/xxx
    const createdDir = allText.match(/Created skill directory:.*\/skills\/([\w-]+)/);
    if (createdDir) return createdDir[1];

    // Priority 4: Last skills/xxx path, filtering out 'skill-creator'
    const allPaths = [...allText.matchAll(/skills\/([\w-]+)/g)].map(m => m[1]).filter(name => name !== 'skill-creator');
    if (allPaths.length > 0) return allPaths[allPaths.length - 1];

    return null;
  };

  // Fetch Skills/DBGPTs list lazily when the picker opens.
  const {
    data: skillsList,
    loading: _loadingSkills,
    runAsync: fetchSkills,
  } = useRequest(
    async () => {
      try {
        const response = await axios.get(`${process.env.API_BASE_URL ?? ''}/api/v1/skills/list`);
        // ctx-axios interceptor returns response.data directly
        if (response?.success && Array.isArray(response.data)) {
          return response.data.map((item: any) => ({
            id: String(item.id || item.name),
            name: normalizeText(item.name),
            description: normalizeText(item.description),
            type: item.type === 'official' ? 'official' : 'personal',
            icon:
              item.skill_type === 'data_analysis'
                ? '📊'
                : item.skill_type === 'coding'
                  ? '💻'
                  : item.skill_type === 'web_search'
                    ? '🔍'
                    : item.skill_type === 'knowledge_qa'
                      ? '📚'
                      : item.skill_type === 'chat'
                        ? '💬'
                        : '⚡',
          })) as Skill[];
        }
        return [];
      } catch (e) {
        console.error('Failed to fetch skills', e);
        return [];
      }
    },
    { manual: true },
  );

  const openSkillPicker = () => {
    if (!skillsList) {
      fetchSkills();
    }
    setIsSkillPanelOpen(true);
  };

  const openKnowledgePicker = () => {
    if (!knowledgeSpaces) {
      fetchKnowledgeSpaces();
    }
    setIsKnowledgePanelOpen(true);
  };

  const openKnowledgeModal = () => {
    if (!knowledgeSpaces) {
      fetchKnowledgeSpaces();
    }
    setIsKnowledgeModalOpen(true);
  };

  const _openDbPicker = () => {
    if (!dataSources) {
      fetchDataSources();
    }
    setIsDbPanelOpen(true);
  };

  const openDbModal = () => {
    if (!dataSources) {
      fetchDataSources();
    }
    setIsDbModalOpen(true);
  };

  const openConnectorPicker = () => {
    setIsConnectorPanelOpen(true);
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    const historyResponseId = router.query.response_id as string | undefined;
    if (!historyResponseId && conversationId) {
      // URL 中 id 消失（如点击 new_task / 探索广场），清空当前会话状态
      setMessages([]);
      setConversationId(null);
      setQuery('');
      setExecutionMap({});
      setActiveMessageId(null);
      setActiveViewMsgId(null);
      setUploadedFiles([]);
      setUploadedFilePath(null);
      setBackendQaUploadActive(false);
      setFilePreview(null);
      setFilePreviewError(null);
      setArtifacts([]);
      setStreamingSummary('');
      setSummaryComplete(false);
      setTaskPlan([]);
      methodHubModeRestoredRef.current = false;
    }
  }, [router.query.response_id]);

  useEffect(() => {
    if (!router.isReady) return;
    const responseId = router.query.response_id as string | undefined;
    if (!responseId) return;
    const sessionId = getResponseHistorySessionId();
    if (!sessionId) return;

    let active = true;
    setHistoryLoading(true);
    getResponseHistory(responseId, sessionId)
      .then(detail => {
        if (!active) return;
        const viewId = `history-view-${detail.response_id}`;
        const restoredTasks: TaskItem[] = [
          ...(detail.spec.objective
            ? [{ content: detail.spec.objective, status: 'completed' as const, priority: 'high' as const }]
            : []),
          ...detail.spec.capability_requirements.map(requirement => ({
            content: requirement.description || requirement.name,
            status: 'completed' as const,
            priority: 'medium' as const,
          })),
        ];
        const restoredOutput =
          detail.output_text || detail.error?.message || 'Completed output was not persisted for this earlier task.';
        const cleanedOutput = cleanFinalContent(restoredOutput);
        setMessages([
          {
            id: `history-human-${detail.response_id}`,
            role: 'human',
            context: detail.input,
          },
          {
            id: viewId,
            role: 'view',
            context: cleanedOutput,
            thinking: false,
            taskPlan: restoredTasks,
            evidence: detail.evidence,
            responseMetadata: detail.metadata,
          },
        ]);
        setConversationId(detail.response_id);
        setTaskPlan(restoredTasks);
        setExecutionMap({});
        setActiveMessageId(viewId);
        setActiveViewMsgId(viewId);
        setStreamingSummary(cleanedOutput);
        setSummaryComplete(true);
        methodHubModeRestoredRef.current = true;
        setMethodHubEnabled(detail.runtime_options.method_hub_enabled);
      })
      .catch(error => {
        if (active) {
          message.error(error instanceof Error ? error.message : t('pg.loadHistoryFailed'));
        }
      })
      .finally(() => {
        if (active) setHistoryLoading(false);
      });

    return () => {
      active = false;
    };
  }, [router.isReady, router.query.response_id, t]);

  useEffect(() => {
    const lastView = [...messages].reverse().find(msg => msg.role === 'view');
    if (lastView?.id) {
      setActiveMessageId(lastView.id);
    }
  }, [messages]);

  useEffect(() => {
    const loadPreview = async () => {
      if (!uploadedFilePath) return;
      if (backendQaUploadActive) return;
      setFilePreviewLoading(true);
      setFilePreviewError(null);
      try {
        const res = await axios.post(`${process.env.API_BASE_URL ?? ''}/api/v1/resource/file/read`, null, {
          params: {
            conv_uid: conversationId || 'preview',
            file_key: uploadedFilePath,
          },
        });
        if (res.data?.success && res.data?.data) {
          let parsed: any;
          try {
            parsed = JSON.parse(res.data.data);
          } catch {
            parsed = res.data.data;
          }
          if (Array.isArray(parsed) && parsed.length > 0) {
            const columns = Object.keys(parsed[0] || {});
            setFilePreview({
              kind: 'table',
              file_name: uploadedFile?.name,
              file_path: uploadedFilePath,
              columns,
              rows: parsed.slice(0, 50),
              shape: [parsed.length, columns.length],
            });
          } else if (typeof parsed === 'string') {
            setFilePreview({
              kind: 'text',
              file_name: uploadedFile?.name,
              file_path: uploadedFilePath,
              text: parsed,
            });
          } else {
            setFilePreview({
              kind: 'text',
              file_name: uploadedFile?.name,
              file_path: uploadedFilePath,
              text: JSON.stringify(parsed, null, 2),
            });
          }
        } else {
          setFilePreviewError(res.data?.err_msg || t('pg.filePreviewFailed'));
        }
      } catch (err: any) {
        setFilePreviewError(err?.message || t('pg.filePreviewFailed'));
      } finally {
        setFilePreviewLoading(false);
      }
    };
    loadPreview();
  }, [uploadedFilePath, conversationId, uploadedFile, backendQaUploadActive]);

  useEffect(() => {
    if (!filePreview || filePreview.kind !== 'table') {
      setChartPreview(null);
      return;
    }
    const rows = filePreview.rows || [];
    const columns = filePreview.columns || [];
    if (!rows.length || !columns.length) {
      setChartPreview(null);
      return;
    }
    const numericColumns = columns.filter(col => {
      const sample = rows.slice(0, 20).map(row => Number(row[col]));
      const numericCount = sample.filter(val => Number.isFinite(val)).length;
      return numericCount >= Math.max(3, Math.floor(sample.length * 0.6));
    });
    if (!numericColumns.length) {
      setChartPreview(null);
      return;
    }
    const yCol = numericColumns[0];
    const xCol = columns.find(col => col !== yCol) || '__index__';
    const data = rows.slice(0, 60).map((row, idx) => {
      const xVal = xCol === '__index__' ? idx + 1 : row[xCol];
      const yVal = Number(row[yCol]);
      return {
        x: typeof xVal === 'string' || typeof xVal === 'number' ? xVal : String(xVal ?? idx + 1),
        y: Number.isFinite(yVal) ? yVal : 0,
      };
    });
    setChartPreview({
      data,
      xField: 'x',
      yField: 'y',
      title: `${yCol} trend`,
    });
  }, [filePreview]);

  useEffect(() => {
    if (!activeMessageId || !filePreview) return;
    const artifactKey = `${activeMessageId}:${filePreview.file_path || filePreview.file_name || ''}`;
    if (artifactKey === lastArtifactKeyRef.current) return;
    lastArtifactKeyRef.current = artifactKey;
    const previewStepId = 'client-preview';
    setExecutionMap(prev => {
      const current = prev[activeMessageId] || { steps: [], outputs: {}, activeStepId: null, collapsed: false };
      const hasStep = current.steps.some(step => step.id === previewStepId);
      const nextSteps = hasStep
        ? current.steps.map(step => (step.id === previewStepId ? { ...step, status: 'done' as const } : step))
        : [
            ...current.steps,
            {
              id: previewStepId,
              step: current.steps.length + 1,
              title: 'Preview & Visualize',
              detail: 'Parsed file preview and prepared visual insights.',
              status: 'done' as const,
            },
          ];
      const outputs = { ...current.outputs };
      const previewOutputs: ExecutionOutput[] = [];
      if (filePreview.kind === 'table') {
        previewOutputs.push({
          output_type: 'table',
          content: {
            columns: (filePreview.columns || []).map(col => ({ title: col, dataIndex: col, key: col })),
            rows: filePreview.rows || [],
          },
        });
      } else if (filePreview.kind === 'text') {
        previewOutputs.push({ output_type: 'text', content: filePreview.text || '' });
      }
      if (chartPreview) {
        previewOutputs.push({
          output_type: 'chart',
          content: {
            data: chartPreview.data,
            xField: chartPreview.xField,
            yField: chartPreview.yField,
          },
        });
      }
      outputs[previewStepId] = previewOutputs;
      return {
        ...prev,
        [activeMessageId]: {
          ...current,
          steps: nextSteps,
          outputs,
          activeStepId: previewStepId,
        },
      };
    });
  }, [activeMessageId, filePreview, chartPreview]);

  interface Round {
    humanMsg: ChatMessage | null;
    viewMsg: ChatMessage | null;
  }

  const rounds = useMemo<Round[]>(() => {
    const result: Round[] = [];
    let i = 0;
    while (i < messages.length) {
      const msg = messages[i];
      if (msg.role === 'human') {
        const next = messages[i + 1];
        if (next && next.role === 'view') {
          result.push({ humanMsg: msg, viewMsg: next });
          i += 2;
        } else {
          result.push({ humanMsg: msg, viewMsg: null });
          i += 1;
        }
      } else if (msg.role === 'view') {
        result.push({ humanMsg: null, viewMsg: msg });
        i += 1;
      } else {
        i += 1;
      }
    }
    return result;
  }, [messages]);

  const selectedViewMsgId = useMemo(() => {
    if (activeViewMsgId) {
      const exists = rounds.some(r => r.viewMsg?.id === activeViewMsgId);
      if (exists) return activeViewMsgId;
    }
    const lastRound = rounds[rounds.length - 1];
    return lastRound?.viewMsg?.id || null;
  }, [activeViewMsgId, rounds]);

  const _getArtifactName = (outputType: string, content: any): string => {
    if (outputType === 'table') {
      const rowCount = content?.rows?.length || 0;
      const colCount = content?.columns?.length || 0;
      return `Data Table (${rowCount} rows × ${colCount} cols)`;
    }
    if (outputType === 'chart') {
      const chartType = content?.chartType || 'line';
      const chartTypeNames: Record<string, string> = {
        line: 'Line Chart',
        column: 'Column Chart',
        bar: 'Bar Chart',
        pie: 'Pie Chart',
        donut: 'Donut Chart',
        area: 'Area Chart',
        scatter: 'Scatter Plot',
        'dual-axes': 'Dual Axes Chart',
      };
      return content?.title || chartTypeNames[chartType] || 'Chart Visualization';
    }
    if (outputType === 'code') {
      return `Code Snippet`;
    }
    if (outputType === 'image') {
      return content?.name || 'Image';
    }
    if (outputType === 'markdown') {
      const preview = String(content).slice(0, 30);
      return `Document: ${preview}${String(content).length > 30 ? '...' : ''}`;
    }
    if (outputType === 'file') {
      return content?.name || content?.file_name || 'File';
    }
    return `${outputType} output`;
  };

  const extractCodeFileName = (code: string, stepLabel: string, index: number): string => {
    const saveMatch = code.match(/\.to_(?:excel|csv)\s*\(\s*['"]([^'"]+)['"]/);
    if (saveMatch) return saveMatch[1].split('/').pop() || saveMatch[1];
    const openMatch = code.match(/open\s*\(\s*['"]([^'"]+\.(?:py|txt|json|csv|xlsx?))['"]/);
    if (openMatch) return openMatch[1].split('/').pop() || openMatch[1];

    const savefigMatch = code.match(/savefig\s*\(\s*['"]([^'"]+)['"]/);
    if (savefigMatch) return savefigMatch[1].split('/').pop() || savefigMatch[1];

    const readMatch = code.match(/pd\.read_(?:csv|excel)\s*\(\s*['"]([^'"]+)['"]/);
    if (readMatch) {
      const srcName = (readMatch[1].split('/').pop() || readMatch[1]).replace(/\.[^.]+$/, '');
      return `analyze_${srcName}.py`;
    }

    const defMatch = code.match(/def\s+(\w+)\s*\(/);
    if (defMatch) return `${defMatch[1]}.py`;

    const classMatch = code.match(/class\s+(\w+)/);
    if (classMatch) return `${classMatch[1]}.py`;

    if (/import\s+matplotlib|plt\./.test(code)) return `visualization_${index + 1}.py`;
    if (/sns\.|import\s+seaborn/.test(code)) return `chart_${index + 1}.py`;
    if (/pd\.|import\s+pandas/.test(code)) return `data_processing_${index + 1}.py`;

    const label = stepLabel.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 30);
    return `${label}_${index}.py`;
  };

  const extractFileReferences = (
    text: string,
  ): Array<{ name: string; downloadable: boolean; size?: number; filePath?: string }> => {
    const refs: Array<{ name: string; downloadable: boolean; size?: number; filePath?: string }> = [];
    const filePattern = /[/\w\-.:]+\.(?:xlsx|xls|csv|py|json|txt|pdf|png|jpg|jpeg|html|md)/gi;
    const matches = text.match(filePattern) || [];
    const seen = new Set<string>();
    matches.forEach(m => {
      const name = m.split('/').pop() || m;
      const lower = name.toLowerCase();
      if (!seen.has(lower)) {
        seen.add(lower);
        // Preserve full path if it looks like an absolute path
        const filePath = m.startsWith('/') ? m : undefined;
        refs.push({ name, downloadable: true, filePath });
      }
    });
    return refs;
  };

  const downloadArtifact = async (artifact: Artifact) => {
    const triggerBlobDownload = (blob: Blob, filename: string) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    };

    switch (artifact.type) {
      case 'image': {
        const imgUrl =
          typeof artifact.content === 'string'
            ? artifact.content
            : artifact.content?.url || artifact.content?.src || String(artifact.content);
        const resolvedUrl = imgUrl.startsWith('/images/') ? `${process.env.API_BASE_URL || ''}${imgUrl}` : imgUrl;
        try {
          const resp = await fetch(resolvedUrl);
          const blob = await resp.blob();
          const filename = artifact.name || imgUrl.split('/').pop() || 'image.png';
          triggerBlobDownload(blob, filename);
        } catch {
          const a = document.createElement('a');
          a.href = resolvedUrl;
          a.download = artifact.name || 'image.png';
          a.target = '_blank';
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
        }
        break;
      }
      case 'html': {
        const htmlContent =
          typeof artifact.content === 'string'
            ? artifact.content
            : artifact.content?.content || artifact.content?.html || String(artifact.content);
        const blob = new Blob([htmlContent], { type: 'text/html' });
        triggerBlobDownload(blob, artifact.name || 'report.html');
        break;
      }
      case 'code': {
        const blob = new Blob([String(artifact.content)], { type: 'text/plain' });
        triggerBlobDownload(blob, artifact.name || 'code.py');
        break;
      }
      case 'table': {
        const rows = artifact.content?.rows || [];
        const columns = artifact.content?.columns?.map((c: any) => c.dataIndex || c.key || c) || [];
        const csvContent = [
          columns.join(','),
          ...rows.map((row: any) => columns.map((col: string) => JSON.stringify(row[col] ?? '')).join(',')),
        ].join('\n');
        const blob = new Blob([csvContent], { type: 'text/csv' });
        triggerBlobDownload(blob, artifact.name?.replace(/\.\w+$/, '.csv') || 'table.csv');
        break;
      }
      case 'markdown':
      case 'summary': {
        const blob = new Blob([String(artifact.content)], { type: 'text/markdown' });
        triggerBlobDownload(blob, artifact.name || `${artifact.type}.md`);
        break;
      }
      case 'file': {
        const filePath = artifact.content?.file_path || artifact.content?.path || (artifact as any).filePath;
        if (filePath && filePath.includes('/images/')) {
          const imgName = filePath.split('/').pop();
          const resolvedUrl = `${process.env.API_BASE_URL || ''}/images/${imgName}`;
          try {
            const resp = await fetch(resolvedUrl);
            const blob = await resp.blob();
            triggerBlobDownload(blob, artifact.name || imgName || 'file');
          } catch {
            message.warning(t('pg.fileNotDownloadable'));
          }
        } else if (filePath) {
          // Download via backend file download endpoint (for agent-created files)
          const downloadUrl = `${process.env.API_BASE_URL || ''}/api/v1/agent/files/download?file_path=${encodeURIComponent(filePath)}`;
          try {
            const resp = await fetch(downloadUrl);
            if (!resp.ok) {
              const errData = await resp.json().catch(() => ({}));
              message.warning(errData.detail || t('pg.fileNotDownloadable'));
              break;
            }
            const blob = await resp.blob();
            triggerBlobDownload(blob, artifact.name || filePath.split('/').pop() || 'file');
          } catch {
            message.warning(t('pg.fileDownloadFailed'));
          }
        } else {
          message.warning(t('pg.fileNotDownloadable'));
        }
        break;
      }
      default: {
        const blob = new Blob([JSON.stringify(artifact.content, null, 2)], { type: 'application/json' });
        triggerBlobDownload(blob, artifact.name || 'artifact.json');
      }
    }
  };

  // Build artifacts from execution data — shared between live streaming and history restore
  const buildArtifactsFromExecution = (
    messageId: string,
    execution: {
      steps: ExecutionStep[];
      outputs: Record<string, ExecutionOutput[]>;
    },
    summaryText?: string,
    filePath?: string | null,
  ): Artifact[] => {
    const finalArtifacts: Artifact[] = [];
    const now = Date.now();
    const seenCodeHashes = new Set<string>();

    if (execution) {
      const allSteps = execution.steps || [];
      allSteps.forEach(step => {
        const stepOutputs = execution.outputs[step.id] || [];
        stepOutputs.forEach((output, oIdx) => {
          if (output.output_type === 'code') {
            const codeStr = String(output.content || '').trim();
            const hash = codeStr.slice(0, 200);
            if (codeStr && !seenCodeHashes.has(hash)) {
              seenCodeHashes.add(hash);
              const fileName = extractCodeFileName(codeStr, step.action || step.id, oIdx);
              finalArtifacts.push({
                id: `${messageId}-code-${step.id}-${oIdx}`,
                type: 'code',
                name: fileName,
                content: codeStr,
                createdAt: now,
                messageId,
                stepId: step.id,
                downloadable: true,
              });
            }
          } else if (output.output_type === 'file') {
            finalArtifacts.push({
              id: `${messageId}-file-${step.id}-${oIdx}`,
              type: 'file',
              name: output.content?.name || output.content?.file_name || 'File',
              content: output.content,
              createdAt: now,
              messageId,
              stepId: step.id,
              downloadable: true,
              size: output.content?.size,
            });
          } else if (output.output_type === 'html') {
            const htmlContent =
              typeof output.content === 'string'
                ? output.content
                : output.content?.content || output.content?.html || String(output.content);
            const htmlTitle = output.content?.title || 'Report';
            finalArtifacts.push({
              id: `${messageId}-html-${step.id}-${oIdx}`,
              type: 'html',
              name: `${htmlTitle}.html`,
              content: htmlContent,
              createdAt: now,
              messageId,
              stepId: step.id,
              downloadable: true,
            });
          } else if (output.output_type === 'image') {
            const imgUrl =
              typeof output.content === 'string'
                ? output.content
                : output.content?.url || output.content?.src || String(output.content);
            const imgName = imgUrl.split('/').pop() || `image_${oIdx}.png`;
            const displayName = imgName.replace(/^[a-f0-9]{8}_/, '');
            finalArtifacts.push({
              id: `${messageId}-img-${step.id}-${oIdx}`,
              type: 'image',
              name: displayName,
              content: imgUrl,
              createdAt: now,
              messageId,
              stepId: step.id,
              downloadable: true,
            });
          }
        });

        // For shell_interpreter steps, extract file paths from code/text outputs
        // and create downloadable file artifacts
        if (step.action === 'shell_interpreter') {
          // Match both absolute paths and relative filenames with extensions
          const absPathPattern = /(?:\/[\w\-.]+)+\.\w{1,10}/g;
          const relFilePattern = /(?:>|>>|\btee\b|\btouch\b)\s+([\w\-./ ]+\.\w{1,10})/g;
          const seenFilePaths = new Set<string>();
          stepOutputs.forEach(output => {
            if (output.output_type === 'code' || output.output_type === 'text') {
              const text = String(output.content || '');
              // Look for file creation patterns
              const hasFileCreation = /(?:>|>>|\btee\b|\bcat\b.*>|\bcp\b|\bmv\b|\btouch\b|\becho\b.*>)/.test(text);
              if (hasFileCreation) {
                const foundPaths: string[] = [];
                // Extract absolute paths
                const absMatches = text.match(absPathPattern) || [];
                foundPaths.push(...absMatches);
                // Extract relative paths after redirection operators
                let relMatch;
                while ((relMatch = relFilePattern.exec(text)) !== null) {
                  const p = relMatch[1].trim();
                  if (p && !p.startsWith('/')) foundPaths.push(p);
                }
                foundPaths.forEach(fp => {
                  // Normalize: strip leading ./ if present
                  const normalized = fp.replace(/^\.\//, '');
                  const fileName = normalized.split('/').pop() || normalized;
                  if (!seenFilePaths.has(fileName.toLowerCase())) {
                    seenFilePaths.add(fileName.toLowerCase());
                    const alreadyHasFile = finalArtifacts.some(
                      a => (a.type === 'file' || a.type === 'image') && a.name.toLowerCase() === fileName.toLowerCase(),
                    );
                    if (!alreadyHasFile) {
                      // Use the path as-is; backend resolves relative paths against pilot/tmp
                      finalArtifacts.push({
                        id: `${messageId}-shellfile-${step.id}-${fileName}`,
                        type: 'file',
                        name: fileName,
                        content: { name: fileName, file_path: normalized },
                        createdAt: now,
                        messageId,
                        stepId: step.id,
                        downloadable: true,
                        filePath: normalized,
                      });
                    }
                  }
                });
              }
            }
          });
        }
      });
    }

    if (summaryText) {
      const fileRefs = extractFileReferences(summaryText);
      fileRefs.forEach((ref, idx) => {
        const alreadyExists = finalArtifacts.some(a => a.name.toLowerCase() === ref.name.toLowerCase());
        if (!alreadyExists) {
          finalArtifacts.push({
            id: `${messageId}-fileref-${idx}`,
            type: 'file',
            name: ref.name,
            content: { name: ref.name, file_path: ref.filePath },
            createdAt: now,
            messageId,
            downloadable: ref.downloadable,
            filePath: ref.filePath,
            size: ref.size,
          });
        }
      });
    }

    if (filePath) {
      const uploadName = filePath.split('/').pop() || 'uploaded_file';
      const alreadyExists = finalArtifacts.some(a => a.name.toLowerCase() === uploadName.toLowerCase());
      if (!alreadyExists) {
        finalArtifacts.push({
          id: `${messageId}-upload`,
          type: 'file',
          name: uploadName,
          content: { name: uploadName, file_path: filePath },
          createdAt: now,
          messageId,
          downloadable: true,
        });
      }
    }

    // Deduplicate: for artifacts with the same name+type, keep only the last one
    const deduped: Artifact[] = [];
    const seen = new Map<string, number>();
    for (let i = finalArtifacts.length - 1; i >= 0; i--) {
      const key = `${finalArtifacts[i].type}:${finalArtifacts[i].name}`;
      if (!seen.has(key)) {
        seen.set(key, i);
        deduped.unshift(finalArtifacts[i]);
      }
    }

    return deduped;
  };

  const handleSpecDecision = async (
    messageId: string,
    confirmation: ResponseConfirmationState,
    action: 'revise' | 'confirm',
    spec: EditableExecutionSpec,
    feedback: string,
  ) => {
    let confirmationAccepted = false;
    setLoading(true);
    setMessages(current =>
      current.map(item =>
        item.id === messageId
          ? { ...item, thinking: true, confirmation: { ...confirmation, submitting: true, error: undefined } }
          : item,
      ),
    );
    try {
      const editedSpec = {
        objective: spec.objective,
        data_requirements: spec.data_requirements,
        capability_requirements: spec.capability_requirements,
        constraints: spec.constraints,
        engine_hint: spec.engine_hint,
      };
      const response = await fetch(
        `${process.env.API_BASE_URL ?? ''}/api/v1/responses/${confirmation.responseId}/decision`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Confirmation-Token': confirmation.token,
          },
          body: JSON.stringify(
            action === 'confirm'
              ? { action, revision: confirmation.revision }
              : {
                  action,
                  revision: confirmation.revision,
                  edited_spec: editedSpec,
                  ...(feedback ? { feedback } : {}),
                },
          ),
        },
      );
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `Request failed with status ${response.status}`);
      }

      if (action === 'confirm') {
        confirmationAccepted = true;
        sessionStorage.removeItem(PENDING_RESPONSE_STORAGE_KEY);
        setMessages(current =>
          current.map(item => (item.id === messageId ? { ...item, thinking: true, confirmation: undefined } : item)),
        );
      }
      if (!response.body) throw new Error('No response body');

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      const processDecisionEvent = (record: string) => {
        const line = record.split('\n').find(item => item.startsWith('data:'));
        if (!line) return;
        const payload = JSON.parse(line.slice(5).trim());
        if (payload.type?.startsWith('pipeline.')) {
          setExecutionMap(current => {
            const execution = current[messageId];
            if (!execution) return current;
            const steps = execution.steps.map(step =>
              step.status === 'running' ? { ...step, status: 'done' as const } : step,
            );
            if (!steps.some(step => step.id === payload.type)) {
              steps.push({
                id: payload.type,
                step: steps.length + 1,
                title: payload.type.replace('pipeline.', '').replaceAll('_', ' '),
                detail: '',
                status: 'running',
              });
            }
            return { ...current, [messageId]: { ...execution, steps, activeStepId: payload.type } };
          });
        } else if (payload.type === 'response.requires_confirmation') {
          const next: ResponseConfirmationState = {
            responseId: payload.response_id,
            token: payload.confirmation_token,
            revision: payload.revision,
            intent: payload.intent?.value || payload.spec?.intent || 'unknown',
            spec: payload.spec,
            expiresAt: payload.expires_at,
          };
          sessionStorage.setItem(
            PENDING_RESPONSE_STORAGE_KEY,
            JSON.stringify({ messageId, responseId: next.responseId, token: next.token }),
          );
          setMessages(current =>
            current.map(item => (item.id === messageId ? { ...item, thinking: false, confirmation: next } : item)),
          );
          setLoading(false);
        } else if (payload.type === 'response.output_text.delta') {
          setMessages(current =>
            current.map(item =>
              item.id === messageId
                ? { ...item, context: item.context + (payload.delta || ''), confirmation: undefined }
                : item,
            ),
          );
        } else if (payload.type === 'response.output_text.done') {
          setMessages(current =>
            current.map(item =>
              item.id === messageId
                ? { ...item, context: cleanFinalContent(payload.text || ''), thinking: false, confirmation: undefined }
                : item,
            ),
          );
        } else if (payload.type === 'response.completed') {
          sessionStorage.removeItem(PENDING_RESPONSE_STORAGE_KEY);
          notifyResponseHistoryChanged();
          setExecutionMap(current => {
            const execution = current[messageId];
            if (!execution) return current;
            return {
              ...current,
              [messageId]: {
                ...execution,
                steps: execution.steps.map(step => ({ ...step, status: 'done' as const })),
                activeStepId: null,
              },
            };
          });
          setLoading(false);
        } else if (payload.type === 'response.failed') {
          throw new Error(payload.error?.message || 'The workflow failed.');
        }
      };

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const records = buffer.split('\n\n');
        buffer = records.pop() || '';
        records.forEach(processDecisionEvent);
      }
    } catch (error) {
      const errorText = error instanceof Error ? error.message : 'Decision failed';
      setLoading(false);
      if (confirmationAccepted) {
        setExecutionMap(current => {
          const execution = current[messageId];
          if (!execution) return current;
          return {
            ...current,
            [messageId]: {
              ...execution,
              steps: execution.steps.map(step =>
                step.status === 'running' ? { ...step, status: 'failed' as const } : step,
              ),
              activeStepId: null,
            },
          };
        });
      }
      setMessages(current =>
        current.map(item =>
          item.id === messageId
            ? {
                ...item,
                thinking: false,
                context: confirmationAccepted ? errorText : item.context,
                confirmation: confirmationAccepted
                  ? undefined
                  : { ...confirmation, submitting: false, error: errorText },
              }
            : item,
        ),
      );
    }
  };

  const handleStart = async (
    inputQuery = query,
    overrideFile?: File | File[] | null,
    overrideSkill?: Skill | null,
    overrideDb?: DataSource | null,
  ) => {
    const effectiveFiles =
      overrideFile !== undefined
        ? Array.isArray(overrideFile)
          ? overrideFile
          : overrideFile
            ? [overrideFile]
            : []
        : uploadedFiles;
    const effectiveSkill = overrideSkill !== undefined ? overrideSkill : selectedSkill;
    const effectiveDb = overrideDb !== undefined ? overrideDb : selectedDb;
    if ((!inputQuery.trim() && effectiveFiles.length === 0) || loading) return;

    let finalQuery = inputQuery;
    const chatMode = 'backend_qa_flow';
    let currentUploadedFilePath: string | null = null;

    // Create the conversation id before upload so files are stored in the same
    // temporary memory that the following Q&A call will read.
    const currentConvId = conversationId || generateUUID();
    const historySessionId = getResponseHistorySessionId() || currentConvId;
    if (!conversationId) {
      setConversationId(currentConvId);
    }

    // Handle File Upload if present
    if (preloadedFilePathRef.current) {
      // Example file already copied to server - skip upload
      currentUploadedFilePath = preloadedFilePathRef.current;
      setUploadedFilePath(currentUploadedFilePath);
      setBackendQaUploadActive(false);
      preloadedFilePathRef.current = null;
      finalQuery = inputQuery || 'Analyze the uploaded file.';
    } else if (effectiveFiles.length > 0) {
      const formData = new FormData();
      formData.append('conv_uid', currentConvId);
      effectiveFiles.forEach(file => {
        formData.append('files', file);
      });

      try {
        const uploadRes = await axios.post(
          `${process.env.API_BASE_URL ?? ''}/api/v1/backend_qa_flow/upload`,
          formData,
          {
            headers: { 'Content-Type': 'multipart/form-data' },
          },
        );

        const resData = (uploadRes as any)?.success !== undefined ? uploadRes : ((uploadRes as any)?.data ?? uploadRes);
        if (resData?.success && resData?.data) {
          currentUploadedFilePath =
            resData.data.conversation_dir ||
            resData.data.files?.[0]?.relative_path ||
            resData.data.files?.[0]?.name ||
            currentConvId;
          setUploadedFilePath(currentUploadedFilePath);
          setBackendQaUploadActive(true);
          finalQuery =
            inputQuery || (effectiveFiles.length > 1 ? 'Analyze the uploaded files.' : 'Analyze the uploaded file.');
        } else if (typeof resData === 'string' && resData.length > 0) {
          // Backend returned the file path directly as a string
          currentUploadedFilePath = resData;
          setUploadedFilePath(currentUploadedFilePath);
          setBackendQaUploadActive(true);
          finalQuery =
            inputQuery || (effectiveFiles.length > 1 ? 'Analyze the uploaded files.' : 'Analyze the uploaded file.');
        } else {
          const errMsg = resData?.err_msg || resData?.message || 'Unknown error';
          message.error('File upload failed: ' + errMsg);
          return;
        }
      } catch (uploadErr: any) {
        console.error('[Upload] error:', uploadErr);
        const errDetail =
          uploadErr?.response?.data?.err_msg ||
          uploadErr?.response?.data?.message ||
          uploadErr?.message ||
          'Network error';
        message.error('File upload failed: ' + errDetail);
        return;
      }
    } else {
      if (uploadedFilePath) {
        setUploadedFilePath(null);
        setFilePreview(null);
      }
      setBackendQaUploadActive(false);
      // Construct context prefix for non-file queries
      const contextParts = [];
      if (effectiveDb) contextParts.push(`[Database: ${effectiveDb.db_name}]`);
      if (selectedKnowledge) contextParts.push(`[Knowledge: ${selectedKnowledge.name}]`);
      if (contextParts.length > 0) {
        finalQuery = `${contextParts.join(' ')} ${inputQuery}`;
      }
    }

    // Calculate current order
    const currentOrder = Math.floor(messages.length / 2) + 1;

    const responseId = generateUUID();

    const humanId = generateUUID();

    // Add user message and AI placeholder message
    setMessages(prev => [
      ...prev,
      {
        id: humanId,
        role: 'human',
        context: inputQuery,
        order: currentOrder,
        attachedFile:
          effectiveFiles.length > 0
            ? {
                name: effectiveFiles.length === 1 ? effectiveFiles[0].name : `${effectiveFiles.length} files selected`,
                size: effectiveFiles.reduce((total, file) => total + file.size, 0),
                type: effectiveFiles.length === 1 ? effectiveFiles[0].type : 'multiple/files',
                count: effectiveFiles.length,
              }
            : undefined,
        attachedKnowledge: selectedKnowledge ?? undefined,
        attachedSkill: effectiveSkill ? { name: effectiveSkill.name, id: effectiveSkill.id } : undefined,
        attachedDb: effectiveDb ? { db_name: effectiveDb.db_name, db_type: effectiveDb.db_type } : undefined,
        attachedConnectors:
          selectedConnectors.length > 0
            ? selectedConnectors.map(c => ({
                id: c.id,
                connector_type: c.connector_type,
                display_name: c.display_name,
              }))
            : undefined,
      },
      {
        id: responseId,
        role: 'view',
        context: '',
        order: currentOrder,
        thinking: true,
      },
    ]);

    setLoading(true);
    setQuery(''); // Clear input
    setStreamingSummary('');
    setActiveViewMsgId(responseId); // Auto-switch right panel to new round

    const controller = new AbortController();
    terminatedStepIdsRef.current.clear();
    setExecutionMap(prev => ({
      ...prev,
      [responseId]: {
        steps: [],
        outputs: {},
        activeStepId: null,
        collapsed: false,
        stepThoughts: {},
      },
    }));
    setActiveMessageId(responseId);

    // Build ext_info once and reuse it for both the live request and the
    // snapshot captured for "保存定时任务", so a saved task replays the exact
    // same context (file / database / knowledge / skill / connectors).
    const extInfo: Record<string, any> = {
      backend_qa_flow: true,
      qa_flow_conv_uid: currentConvId,
      ...(currentUploadedFilePath ? { file_path: currentUploadedFilePath } : {}),
      ...(effectiveFiles.length > 0
        ? { file_names: effectiveFiles.map(file => file.name), file_count: effectiveFiles.length }
        : {}),
      ...(effectiveSkill ? { skill_id: effectiveSkill.id, skill_name: effectiveSkill.name } : {}),
      ...(effectiveDb ? { database_name: effectiveDb.db_name, database_type: effectiveDb.db_type } : {}),
      ...(selectedKnowledge
        ? { knowledge_space_name: selectedKnowledge.name, knowledge_space_id: selectedKnowledge.id }
        : {}),
      ...(selectedConnectors.length > 0 ? { connector_ids: selectedConnectors.map(c => c.id) } : {}),
    };
    const selectParam = '';

    // Snapshot the exact payload being sent (minus the per-run conv_uid, which
    // each scheduled run regenerates) so buildSnapshot replays this real run.
    lastSentPayloadRef.current = {
      version: 1,
      user_input: finalQuery,
      chat_mode: chatMode,
      model_name: model,
      select_param: selectParam,
      temperature: 0.6,
      max_new_tokens: 4000,
      ext_info: extInfo,
    };

    try {
      const response = await fetch(`${process.env.API_BASE_URL ?? ''}/api/v1/responses`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          input: finalQuery,
          data_corpus_package: {
            sources: currentUploadedFilePath ? [currentUploadedFilePath] : [],
            schemas: {},
            metadata: {},
          },
          session_id: historySessionId,
          runtime_options: {
            method_hub_enabled: methodHubEnabled,
          },
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        let errorMessage = `Request failed with status ${response.status}`;
        try {
          const errorPayload = await response.json();
          if (typeof errorPayload?.detail === 'string') errorMessage = errorPayload.detail;
        } catch {
          // Keep the status-based fallback when the error body is not JSON.
        }
        throw new Error(errorMessage);
      }

      if (!response.body) {
        throw new Error('No response body');
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      const processEvent = (raw: string) => {
        const dataLine = raw.split('\n').find(line => line.startsWith('data:'));
        if (!dataLine) return;
        const data = dataLine.slice(5).trim();
        if (!data) return;
        let payload: any;
        try {
          payload = JSON.parse(data);
        } catch (_err) {
          return;
        }
        if (payload.type === 'response.created') return;
        if (payload.type === 'response.requires_confirmation') {
          const confirmation: ResponseConfirmationState = {
            responseId: payload.response_id,
            token: payload.confirmation_token,
            revision: payload.revision,
            intent: payload.intent?.value || payload.spec?.intent || 'unknown',
            spec: payload.spec,
            expiresAt: payload.expires_at,
          };
          sessionStorage.setItem(
            PENDING_RESPONSE_STORAGE_KEY,
            JSON.stringify({ messageId: responseId, responseId: confirmation.responseId, token: confirmation.token }),
          );
          setMessages(prev =>
            prev.map(msg =>
              msg.id === responseId && msg.role === 'view' ? { ...msg, thinking: false, confirmation } : msg,
            ),
          );
          setExecutionMap(prev => {
            const current = prev[responseId];
            if (!current) return prev;
            return {
              ...prev,
              [responseId]: {
                ...current,
                steps: current.steps.map(step =>
                  step.status === 'running' ? { ...step, status: 'done' as const } : step,
                ),
                activeStepId: null,
              },
            };
          });
          setLoading(false);
          return;
        }
        if (payload.type?.startsWith('pipeline.')) {
          const pipelineLabels: Record<string, string> = {
            'pipeline.start': 'Starting workflow',
            'pipeline.intent_analyzed': 'Understanding intent',
            'pipeline.spec_built': 'Planning execution',
            'pipeline.spec_confirmed': 'Confirming plan',
            'pipeline.engine_selected': 'Selecting engine',
            'pipeline.engine_completed': 'Running analysis',
            'pipeline.evidence_collected': 'Collecting evidence',
            'pipeline.completed': 'Finalizing response',
          };
          setExecutionMap(prev => {
            const current = prev[responseId];
            if (!current) return prev;
            const existing = current.steps.some(step => step.id === payload.type);
            const steps = current.steps.map(step =>
              step.status === 'running' ? { ...step, status: 'done' as const } : step,
            );
            if (!existing) {
              steps.push({
                id: payload.type,
                step: steps.length + 1,
                title: pipelineLabels[payload.type] || payload.type,
                detail: '',
                status: 'running',
              });
            }
            return {
              ...prev,
              [responseId]: { ...current, steps, activeStepId: payload.type },
            };
          });
          return;
        }
        if (payload.type === 'response.output_text.delta') {
          setMessages(prev =>
            prev.map(msg =>
              msg.id === responseId && msg.role === 'view'
                ? { ...msg, context: msg.context + (payload.delta || ''), thinking: true }
                : msg,
            ),
          );
          return;
        }
        if (payload.type === 'response.output_text.done') {
          payload = { type: 'final', content: payload.text || '' };
        } else if (payload.type === 'response.completed') {
          setLoading(false);
          notifyResponseHistoryChanged();
          return;
        } else if (payload.type === 'response.failed') {
          throw new Error(payload.error?.message || 'The workflow failed.');
        }
        if (payload.type === 'context.status') {
          const budget = Number(payload.budget ?? 0);
          if (!Number.isFinite(budget) || budget <= 0) {
            setContextStatus(null);
            return;
          }
          const stateMap: Record<string, 'OK' | 'WARNING' | 'ERROR'> = {
            normal: 'OK',
            warning: 'WARNING',
            error: 'ERROR',
            critical: 'ERROR',
            overflow: 'ERROR',
          };
          setContextStatus({
            state: stateMap[payload.state] || 'OK',
            used_tokens: payload.used ?? 0,
            max_tokens: budget,
            usage_percent: (payload.ratio ?? 0) * 100,
            layer: payload.compact_layer ?? null,
          });
          return;
        }
        if (payload.type === 'plan.update') {
          if (Array.isArray(payload.tasks)) {
            const nextTasks = payload.tasks as TaskItem[];
            setTaskPlan(nextTasks);
            setMessages(prev =>
              prev.map(msg => {
                if (msg.id !== responseId || msg.role !== 'view') return msg;
                return { ...msg, taskPlan: nextTasks };
              }),
            );
          }
          return;
        }
        if (payload.type === 'step.start') {
          const id = payload.id || `${payload.step}`;
          if (terminatedStepIdsRef.current.has(id)) return;
          setExecutionMap(prev => {
            const current = prev[responseId] || {
              steps: [],
              outputs: {},
              activeStepId: null,
              collapsed: false,
              stepThoughts: {},
            };
            const existingThoughts = current.stepThoughts || {};
            const nextThoughts = existingThoughts;
            // Check if step already exists - if so, update it (especially phase) instead of creating duplicate
            const existingStepIndex = current.steps.findIndex(s => s.id === id);
            let nextSteps;
            if (existingStepIndex >= 0) {
              // Update existing step with new title/phase
              nextSteps = current.steps.map((step, idx) =>
                idx === existingStepIndex
                  ? {
                      ...step,
                      title: payload.title,
                      detail: payload.detail,
                      phase: payload.phase,
                      todoMeta: payload.todo_meta || step.todoMeta,
                      status: 'running' as const,
                    }
                  : step.status === 'running'
                    ? { ...step, status: 'done' }
                    : step,
              );
            } else {
              // New step - mark running steps as done and add new step
              nextSteps = [
                ...current.steps.map(item => (item.status === 'running' ? { ...item, status: 'done' } : item)),
                {
                  id,
                  step: payload.step,
                  title: payload.title,
                  detail: payload.detail,
                  phase: payload.phase,
                  todoMeta: payload.todo_meta,
                  status: 'running' as const,
                  action: payload.action,
                },
              ];
            }
            return {
              ...prev,
              [responseId]: {
                ...current,
                steps: nextSteps,
                outputs: { ...current.outputs, [id]: current.outputs[id] || [] },
                stepThoughts: nextThoughts,
                // Only auto-focus for existing step updates (e.g., "思考中" -> "sql_query").
                // New placeholder steps wait for step.meta to get real content before stealing focus.
                activeStepId: existingStepIndex >= 0 ? id : current.activeStepId || id,
              },
            };
          });
          setActiveMessageId(responseId);
          setRightPanelCollapsed(false);
        } else if (payload.type === 'step.meta') {
          if (payload.action && payload.action.toLowerCase() === 'terminate') {
            terminatedStepIdsRef.current.add(payload.id);
            setExecutionMap(prev => {
              const current = prev[responseId];
              if (!current) return prev;
              const nextSteps = current.steps.filter(item => item.id !== payload.id);
              const nextActiveStepId = current.activeStepId === payload.id ? null : current.activeStepId;
              return {
                ...prev,
                [responseId]: { ...current, steps: nextSteps, activeStepId: nextActiveStepId },
              };
            });
            return;
          }
          // Clear manual step selection so the right panel auto-tracks this step
          if (payload.action) {
            setSelectedStepId(null);
          }
          setExecutionMap(prev => {
            const current = prev[responseId];
            if (!current) return prev;
            // Build detail from action only (thought goes to stepThoughts)
            const nextSteps = current.steps.map(item => {
              if (item.id !== payload.id) return item;
              const parts = [] as string[];
              if (payload.action) {
                parts.push(`Action: ${payload.action}`);
                if (
                  payload.action !== 'code_interpreter' &&
                  payload.action !== 'shell_interpreter' &&
                  payload.action_input
                ) {
                  const inputStr =
                    typeof payload.action_input === 'string'
                      ? payload.action_input
                      : JSON.stringify(payload.action_input, null, 2);
                  parts.push(`Action Input: ${inputStr}`);
                }
              }
              return {
                ...item,
                title: payload.title || item.title,
                detail: parts.join('\n') || item.detail,
                action: payload.action || item.action,
                actionInput: payload.action_input || item.actionInput,
                todoMeta: payload.todo_meta || item.todoMeta,
              };
            });
            // Route model-provided action display fields to the subtle status row.
            const displayThought = payload.action_intention
              ? payload.action_reason
                ? `${payload.action_intention}\n${payload.action_reason}`
                : payload.action_intention
              : payload.thought;
            const nextThoughts = displayThought
              ? {
                  ...current.stepThoughts,
                  [payload.id]: displayThought,
                }
              : current.stepThoughts;
            return {
              ...prev,
              [responseId]: {
                ...current,
                steps: nextSteps,
                stepThoughts: nextThoughts,
                // Focus right panel on this step when it receives action content
                ...(payload.action ? { activeStepId: payload.id } : {}),
              },
            };
          });
        } else if (payload.type === 'step.output') {
          if (terminatedStepIdsRef.current.has(payload.id || '')) return;
          setExecutionMap(prev => {
            const current = prev[responseId];
            if (!current) return prev;
            const targetId = current.activeStepId;
            if (!targetId) return prev;
            const nextSteps = current.steps.map(item => {
              if (item.id !== targetId) return item;
              const detail = `${item.detail}\n${payload.detail}`.trim();
              return { ...item, detail };
            });
            return { ...prev, [responseId]: { ...current, steps: nextSteps } };
          });
        } else if (payload.type === 'step.chunk') {
          const id = payload.id;
          if (terminatedStepIdsRef.current.has(id || '')) return;
          setExecutionMap(prev => {
            const current = prev[responseId];
            if (!current) return prev;
            const targetId = id || current.activeStepId;
            if (!targetId) return prev;
            const list = current.outputs[targetId] ? [...current.outputs[targetId]] : [];
            list.push({ output_type: payload.output_type, content: payload.content });
            return {
              ...prev,
              [responseId]: {
                ...current,
                outputs: { ...current.outputs, [targetId]: list },
              },
            };
          });

          // Artifacts are now generated at task completion (final event),
          // not during streaming — to avoid showing intermediate outputs as artifacts
        } else if (payload.type === 'step.done') {
          const id = payload.id;
          if (terminatedStepIdsRef.current.has(id || '')) return;
          setExecutionMap(prev => {
            const current = prev[responseId];
            if (!current) return prev;
            const targetId = id || current.activeStepId;
            if (!targetId) return prev;
            const nextSteps = current.steps.map(item =>
              item.id === targetId ? { ...item, status: payload.status || 'done' } : item,
            );
            return { ...prev, [responseId]: { ...current, steps: nextSteps } };
          });
        } else if (payload.type === 'step.thought') {
          const content = payload.content || '';
          let normalizedThought = '';
          if (typeof content === 'string') {
            normalizedThought = content;
          } else if (content && typeof content === 'object') {
            const todoValue = (content as Record<string, unknown>).TODO;
            if (typeof todoValue === 'string') {
              normalizedThought = todoValue;
            } else {
              try {
                normalizedThought = JSON.stringify(content);
              } catch {
                normalizedThought = String(content);
              }
            }
          }
          if (normalizedThought) {
            setExecutionMap(prev => {
              const current = prev[responseId];
              if (!current) return prev;
              const targetId = payload.id || current.activeStepId || 'initial';
              return {
                ...prev,
                [responseId]: {
                  ...current,
                  stepThoughts: {
                    ...current.stepThoughts,
                    [targetId]: (current.stepThoughts?.[targetId] || '') + normalizedThought,
                  },
                },
              };
            });
          }
        } else if (payload.type === 'final') {
          setExecutionMap(prev => {
            const current = prev[responseId];
            if (!current) return prev;
            const nextSteps = current.steps.map(item =>
              item.status === 'running' ? { ...item, status: 'done' } : item,
            );
            return { ...prev, [responseId]: { ...current, steps: nextSteps } };
          });
          setMessages(prev =>
            prev.map(msg => {
              if (msg.id !== responseId || msg.role !== 'view') return msg;
              return {
                ...msg,
                context: cleanFinalContent(payload.content || ''),
                thinking: false,
              };
            }),
          );
          setTaskPlan([]);
          setActiveMessageId(responseId);

          if (payload.content && payload.content.trim()) {
            setStreamingSummary('');
            setSummaryComplete(false);
            setRightPanelView('summary');

            const summaryText = cleanFinalContent(payload.content);
            const streamInterval = setInterval(() => {
              setStreamingSummary(prev => {
                if (prev.length >= summaryText.length) {
                  clearInterval(streamInterval);
                  setSummaryComplete(true);

                  setExecutionMap(currentExecMap => {
                    const execution = currentExecMap[responseId];
                    const deduped = buildArtifactsFromExecution(
                      responseId,
                      execution || { steps: [], outputs: {} },
                      summaryText,
                      uploadedFilePath,
                    );

                    setArtifacts(prevArtifacts => {
                      const filtered = prevArtifacts.filter(a => a.messageId !== responseId);
                      const newArtifacts = [...filtered, ...deduped];

                      // Auto-select the first HTML artifact for preview, or image if no HTML
                      const htmlArtifact = deduped.find(a => a.type === 'html');
                      if (htmlArtifact) {
                        setPreviewArtifact(htmlArtifact as Artifact);
                        setRightPanelView('html-preview');
                        setRightPanelCollapsed(false);
                      } else {
                        const imgArtifact = deduped.find(a => a.type === 'image');
                        if (imgArtifact) {
                          setPreviewArtifact(imgArtifact as Artifact);
                          setRightPanelView('image-preview');
                          setRightPanelCollapsed(false);
                        }
                      }

                      return newArtifacts;
                    });

                    // Detect skill creation from shell_interpreter steps
                    if (execution) {
                      const isSkillPackageStep = (s: ExecutionStep) => {
                        if (s.action !== 'shell_interpreter') return false;
                        // Check detail, actionInput, and outputs for package_skill/init_skill
                        const detailHas = s.detail?.includes('package_skill') || s.detail?.includes('init_skill');
                        const inputHas =
                          s.actionInput?.includes('package_skill') || s.actionInput?.includes('init_skill');
                        const outputTexts = (execution.outputs[s.id] || []).map(o => String(o.content)).join(' ');
                        const outputHas =
                          outputTexts.includes('package_skill') ||
                          outputTexts.includes('init_skill') ||
                          outputTexts.includes('Successfully packaged');
                        return detailHas || inputHas || outputHas;
                      };
                      const skillStep = (execution.steps || []).find(isSkillPackageStep);
                      if (skillStep) {
                        // Extract skill name from actionInput, detail, or outputs
                        const allText = [
                          skillStep.actionInput || '',
                          skillStep.detail || '',
                          ...(execution.outputs[skillStep.id] || []).map(o => String(o.content)),
                        ].join(' ');
                        const skillName = extractCreatedSkillName(allText);
                        if (skillName) {
                          setCreatedSkillNames(prev => ({ ...prev, [responseId]: skillName }));
                          setRightPanelView('skill-preview');
                        }
                      }
                    }

                    return currentExecMap;
                  });

                  return prev;
                }
                const chunkSize = Math.min(3, summaryText.length - prev.length);
                return prev + summaryText.slice(prev.length, prev.length + chunkSize);
              });
            }, 15);
          }
        } else if (payload.type === 'done') {
          setLoading(false);
        }
      };

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';
        parts.forEach(processEvent);
      }
      setLoading(false);
    } catch (err: any) {
      setLoading(false);
      message.error(err?.message || 'Failed to get response');
      setMessages(prev => {
        const newMessages = [...prev];
        const lastMsg = newMessages[newMessages.length - 1];
        if (lastMsg && lastMsg.role === 'view') {
          lastMsg.context = err?.message || 'Error occurred';
          lastMsg.thinking = false;
        }
        return newMessages;
      });
    }
  };

  const handleExampleClick = async (example: (typeof EXAMPLE_CARDS)[number]) => {
    const queryKey = `example_${example.id}_query`;
    const queryVal = t(queryKey) as string;
    const translatedQuery = (queryVal && queryVal !== queryKey ? queryVal : example.query) as string;

    if (loading) return;

    try {
      message.loading({ content: t('pg.loadingExample'), key: 'example-loading', duration: 0 });

      let filePath: string | null = null;
      let fakeFile: File | null = null;

      // If example has a file, request it from backend
      if (example.fileName) {
        const res = await axios.post(`${process.env.API_BASE_URL ?? ''}/api/v1/examples/use`, {
          example_id: example.id,
        });

        if (res?.success && res?.data) {
          filePath = res.data;
          preloadedFilePathRef.current = filePath;
          fakeFile = new File([new ArrayBuffer(example.fileSize || 0)], example.fileName, {
            type: example.fileType,
          });
          setUploadedFiles([fakeFile]);
        } else {
          message.destroy('example-loading');
          const errMsg = res?.err_msg || 'Unknown error';
          message.error(t('pg.loadExampleFailed', { error: errMsg }));
          return;
        }
      }

      message.destroy('example-loading');

      // Auto-select skill if example specifies one
      let exampleSkill: Skill | null = null;
      if (example.skillName) {
        const availableSkills = skillsList || (await fetchSkills());
        const matched = availableSkills?.find(s => s.name === example.skillName);
        if (matched) {
          exampleSkill = matched;
          setSelectedSkill(matched);
        }
      }

      // Auto-select database if example specifies one
      let matchedDb: DataSource | null = null;
      if (example.dbName) {
        const availableDataSources = dataSources || (await fetchDataSources());
        const found = availableDataSources?.find((ds: DataSource) => ds.db_name === example.dbName);
        if (found) {
          matchedDb = found;
          setSelectedDb(found);
        }
      }

      handleStart(translatedQuery, fakeFile, exampleSkill, matchedDb);
    } catch (err: unknown) {
      message.destroy('example-loading');
      console.error('Example click error:', err);
      const errMessage = err instanceof Error ? err.message : 'Unknown error';
      message.error(t('pg.loadExampleFailed', { error: errMessage }));
    }
  };

  // Clear chat history
  const handleClearChat = () => {
    setMessages([]);
    setConversationId(null);
    setQuery('');
    setExecutionMap({});
    setActiveMessageId(null);
    setActiveViewMsgId(null);
    setUploadedFiles([]);
    setUploadedFilePath(null);
    setBackendQaUploadActive(false);
    setFilePreview(null);
    setFilePreviewError(null);
    setArtifacts([]);
    setStreamingSummary('');
    setSummaryComplete(false);
    router.push('/', undefined, { shallow: true });
  };

  // Build snapshot of current conversation state for scheduled task creation
  const buildSnapshot = (): ChatReplayPayload => {
    // Prefer the payload actually sent to the agent this session — it carries
    // the real execution context (file_path / database / knowledge / skill /
    // connectors) and is immune to UI state changed after sending.
    if (lastSentPayloadRef.current) {
      return lastSentPayloadRef.current;
    }
    // Fallback (e.g. conversation restored from history, where no send
    // happened this session): reconstruct from the first question + current
    // selections. Keeps the old behavior so this path never regresses.
    const firstUserMsg = messages.find(m => m.role === 'human');
    return {
      version: 1,
      user_input: firstUserMsg?.context ?? '',
      chat_mode: 'backend_qa_flow',
      model_name: model,
      select_param: '',
      ext_info: {
        backend_qa_flow: true,
        qa_flow_conv_uid: conversationId,
        ...(uploadedFilePath ? { file_path: uploadedFilePath } : {}),
        ...(uploadedFiles.length > 0
          ? { file_names: uploadedFiles.map(file => file.name), file_count: uploadedFiles.length }
          : {}),
        ...(selectedSkill ? { skill_id: selectedSkill.id, skill_name: selectedSkill.name } : {}),
        ...(selectedDb ? { database_name: selectedDb.db_name, database_type: selectedDb.db_type } : {}),
        ...(selectedKnowledge
          ? { knowledge_space_name: selectedKnowledge.name, knowledge_space_id: selectedKnowledge.id }
          : {}),
        ...(selectedConnectors.length > 0 ? { connector_ids: selectedConnectors.map(c => c.id) } : {}),
      },
    };
  };

  const getDbIcon = (type: string) => {
    const lowerType = type.toLowerCase();
    if (lowerType.includes('mysql')) return <ConsoleSqlOutlined className='text-blue-500' />;
    if (lowerType.includes('postgre')) return <DatabaseOutlined className='text-blue-400' />;
    if (lowerType.includes('mongo')) return <CloudServerOutlined className='text-green-500' />;
    if (lowerType.includes('sqlite')) return <DatabaseOutlined className='text-amber-500' />;
    return <DatabaseOutlined className='text-gray-500' />;
  };

  const getFileKey = (file: File) => `${file.name}:${file.size}:${file.lastModified || 0}`;

  const parseLocalFilePreview = (file: File) => {
    const lowerName = file.name.toLowerCase();
    const textLikeExtensions = [
      '.csv',
      '.tsv',
      '.txt',
      '.md',
      '.json',
      '.jsonl',
      '.log',
      '.sql',
      '.py',
      '.js',
      '.jsx',
      '.ts',
      '.tsx',
      '.html',
      '.css',
      '.xml',
      '.yaml',
      '.yml',
    ];
    const isTextLike = textLikeExtensions.some(ext => lowerName.endsWith(ext)) || file.type.startsWith('text/');

    setFilePreviewError(null);

    if (lowerName.endsWith('.zip')) {
      setFilePreview({
        kind: 'text',
        file_name: file.name,
        text: `${file.name} selected. The backend will extract and index it for this conversation.`,
      });
      return;
    }

    if (!isTextLike || file.size > 2 * 1024 * 1024) {
      setFilePreview({
        kind: 'text',
        file_name: file.name,
        text: `${file.name} selected. It will be uploaded and indexed for this conversation.`,
      });
      return;
    }

    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || '');
      if (lowerName.endsWith('.csv') || lowerName.endsWith('.tsv')) {
        const separator = lowerName.endsWith('.tsv') ? '\t' : ',';
        const lines = text.split(/\r?\n/).filter(Boolean);
        const columns = (lines[0] || '').split(separator).map(col => col.trim());
        if (columns.length > 1 && lines.length > 1) {
          const rows = lines.slice(1, 51).map(line => {
            const values = line.split(separator);
            return columns.reduce<Record<string, any>>((row, column, index) => {
              row[column || `column_${index + 1}`] = values[index] ?? '';
              return row;
            }, {});
          });
          setFilePreview({
            kind: 'table',
            file_name: file.name,
            columns,
            rows,
            shape: [Math.max(0, lines.length - 1), columns.length],
          });
          return;
        }
      }
      setFilePreview({
        kind: 'text',
        file_name: file.name,
        text: text.slice(0, 6000),
      });
    };
    reader.onerror = () => {
      setFilePreview({
        kind: 'text',
        file_name: file.name,
        text: `${file.name} selected. Local preview is unavailable, but upload can continue.`,
      });
    };
    reader.readAsText(file);
  };

  const clearUploadedFiles = () => {
    setUploadedFiles([]);
    setUploadedFilePath(null);
    setBackendQaUploadActive(false);
    setFilePreview(null);
    setFilePreviewError(null);
  };

  const removeUploadedFile = (targetFile: File) => {
    const targetKey = getFileKey(targetFile);
    const remainingFiles = uploadedFiles.filter(file => getFileKey(file) !== targetKey);
    setUploadedFiles(remainingFiles);
    if (remainingFiles.length === 0) {
      setUploadedFilePath(null);
      setBackendQaUploadActive(false);
      setFilePreview(null);
      setFilePreviewError(null);
    } else if (uploadedFile && getFileKey(uploadedFile) === targetKey) {
      parseLocalFilePreview(remainingFiles[0]);
    }
  };

  const appendUploadedFiles = (files: File[]) => {
    const selectedFiles = files.filter(Boolean);
    if (selectedFiles.length === 0) return;

    setUploadedFiles(prev => {
      const fileMap = new Map(prev.map(file => [getFileKey(file), file]));
      selectedFiles.forEach(file => fileMap.set(getFileKey(file), file));
      return Array.from(fileMap.values());
    });
    setUploadedFilePath(null);
    setBackendQaUploadActive(false);
    parseLocalFilePreview(uploadedFile || selectedFiles[0]);

    const zipCount = selectedFiles.filter(file => file.name.toLowerCase().endsWith('.zip')).length;
    const suffix = zipCount > 0 ? ' Zip files will be extracted on upload.' : '';
    message.success(`${selectedFiles.length} file${selectedFiles.length > 1 ? 's' : ''} attached.${suffix}`);
  };

  const renderUploadedFileTags = () => {
    const visibleFiles = uploadedFiles.slice(0, 6);
    return (
      <>
        {visibleFiles.map(file => (
          <Tag
            key={getFileKey(file)}
            closable
            onClose={() => removeUploadedFile(file)}
            className='flex items-center gap-1 bg-green-50 border-green-200 text-green-700 px-3 py-1 rounded-full max-w-[220px]'
          >
            {_getFileIcon(file.name, file.type)} <span className='font-medium ml-1 truncate'>{file.name}</span>
          </Tag>
        ))}
        {uploadedFiles.length > visibleFiles.length && (
          <Tag
            closable
            onClose={clearUploadedFiles}
            className='flex items-center gap-1 bg-green-50 border-green-200 text-green-700 px-3 py-1 rounded-full'
          >
            <FileOutlined />{' '}
            <span className='font-medium ml-1'>+{uploadedFiles.length - visibleFiles.length} files</span>
          </Tag>
        )}
      </>
    );
  };

  const uploadAccept =
    '.zip,.csv,.tsv,.txt,.md,.json,.jsonl,.xlsx,.xls,.pdf,.docx,.sql,.py,.js,.jsx,.ts,.tsx,.html,.css,.xml,.yaml,.yml,.log';

  const openUploadDialog = () => {
    uploadInputRef.current?.click();
  };

  const handleNativeUploadChange = (event: ChangeEvent<HTMLInputElement>) => {
    appendUploadedFiles(Array.from(event.target.files || []));
    event.target.value = '';
  };

  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: '#000000',
        },
      }}
    >
      <>
        <input
          ref={uploadInputRef}
          data-testid='backend-qa-file-input'
          type='file'
          multiple
          accept={uploadAccept}
          className='hidden'
          onChange={handleNativeUploadChange}
        />
        <div className='flex h-full w-full bg-[#f7f7f9] dark:bg-[#0f1012] text-[#1a1b1e] dark:text-gray-200 font-sans overflow-hidden'>
          {/* Main Content */}
          <div className='flex-1 flex flex-col relative overflow-hidden bg-white dark:bg-[#111217]'>
            {/* From Task Banner - shown when navigating from a scheduled task */}
            {router.query.from_task && <FromTaskBanner taskId={router.query.from_task as string} />}

            {/* Chat Messages or Hero Section */}
            {/* When from_task mode and loading history, show loading spinner instead of Hero */}
            {(router.query.from_task || router.query.response_id) && historyLoading && messages.length === 0 ? (
              <div className='flex-1 flex items-center justify-center'>
                <Spin size='large' tip={t('pg.loadingConversationHistory')} />
              </div>
            ) : messages.length > 0 ? (
              <div className={`flex-1 flex overflow-hidden ${rightPanelCollapsed ? 'justify-center' : ''}`}>
                <div
                  className={`${rightPanelCollapsed ? 'flex-1 max-w-[800px] border-r-0' : 'flex-[2] min-w-0 border-r border-gray-200/80 dark:border-gray-800'} flex flex-col overflow-hidden bg-white dark:bg-[#111217] transition-all duration-300 relative`}
                >
                  <div className='flex-1 min-h-0 overflow-y-auto'>
                    {rounds.map((round, roundIndex) => {
                      const isLastRound = roundIndex === rounds.length - 1;
                      const isSelected = round.viewMsg?.id === selectedViewMsgId;
                      const isCurrentRoundCollapsed = !isLastRound && !isSelected;

                      const execution = round.viewMsg?.id ? executionMap[round.viewMsg.id] : undefined;
                      const {
                        sections,
                        activeStep: _activeStep,
                        outputs: _outputs,
                        stepThoughts,
                      } = convertToManusFormat(execution, round.humanMsg?.context, t);
                      const isWorking =
                        (isLastRound &&
                          (round.viewMsg?.thinking || execution?.steps.some(s => s.status === 'running'))) ||
                        false;

                      const roundAssistantText = isLastRound
                        ? streamingSummary || round.viewMsg?.context || undefined
                        : round.viewMsg?.context || undefined;

                      return (
                        <div key={round.viewMsg?.id || round.humanMsg?.id || `round-${roundIndex}`}>
                          <ManusLeftPanel
                            sections={sections}
                            activeStepId={isSelected ? selectedStepId || execution?.activeStepId : undefined}
                            onStepClick={(stepId, _sectionId) => {
                              if (round.viewMsg?.id) {
                                setActiveViewMsgId(round.viewMsg.id);
                                setSelectedStepId(stepId);
                                setRightPanelCollapsed(false);
                                setExecutionMap(prev => ({
                                  ...prev,
                                  [round.viewMsg!.id!]: {
                                    ...prev[round.viewMsg!.id!],
                                    activeStepId: stepId,
                                  },
                                }));
                              }
                            }}
                            isWorking={isWorking}
                            userQuery={round.humanMsg?.context}
                            attachedFile={round.humanMsg?.attachedFile}
                            attachedKnowledge={round.humanMsg?.attachedKnowledge}
                            attachedSkill={round.humanMsg?.attachedSkill}
                            attachedDb={round.humanMsg?.attachedDb}
                            taskPlan={round.viewMsg?.taskPlan}
                            attachedConnectors={round.humanMsg?.attachedConnectors}
                            assistantText={roundAssistantText}
                            modelName={round.viewMsg?.model_name || model}
                            stepThoughts={stepThoughts}
                            artifacts={artifacts.filter(a => a.messageId === round.viewMsg?.id)}
                            onArtifactClick={artifact => {
                              if (round.viewMsg?.id) setActiveViewMsgId(round.viewMsg.id);
                              setRightPanelCollapsed(false);
                              if (artifact.type === 'html') {
                                setPreviewArtifact(artifact as Artifact);
                                setRightPanelView('html-preview');
                              } else if (artifact.type === 'code' && artifact.stepId) {
                                setSelectedStepId(artifact.stepId);
                                setRightPanelView('execution');
                                if (round.viewMsg?.id && execution) {
                                  setExecutionMap(prev => ({
                                    ...prev,
                                    [round.viewMsg!.id!]: {
                                      ...prev[round.viewMsg!.id!],
                                      activeStepId: artifact.stepId!,
                                    },
                                  }));
                                }
                              } else if (artifact.type === 'file') {
                                // Image file artifacts: preview instead of download
                                if (/\.(png|jpg|jpeg|gif|webp|svg|bmp)$/i.test(artifact.name)) {
                                  setPreviewArtifact(artifact as Artifact);
                                  setRightPanelView('image-preview');
                                } else {
                                  downloadArtifact(artifact as Artifact);
                                }
                              } else if (artifact.type === 'image') {
                                setPreviewArtifact(artifact as Artifact);
                                setRightPanelView('image-preview');
                              }
                            }}
                            onArtifactDownload={artifact => downloadArtifact(artifact as Artifact)}
                            onViewAllFiles={() => {
                              if (round.viewMsg?.id) setActiveViewMsgId(round.viewMsg.id);
                              setRightPanelCollapsed(false);
                              setRightPanelView('files');
                            }}
                            isCollapsed={isCurrentRoundCollapsed}
                            onExpand={() => {
                              if (round.viewMsg?.id) setActiveViewMsgId(round.viewMsg.id);
                            }}
                            createdSkillName={createdSkillNames[round.viewMsg?.id || '']}
                            onSkillCardClick={_skillName => {
                              if (round.viewMsg?.id) setActiveViewMsgId(round.viewMsg.id);
                              setRightPanelCollapsed(false);
                              setRightPanelView('skill-preview');
                              // Find the package_skill step and select it so right panel shows SkillCardRenderer
                              if (execution) {
                                const skillStep = execution.steps.find((s: ExecutionStep) => {
                                  if (s.action !== 'shell_interpreter') return false;
                                  const detailHas =
                                    s.detail?.includes('package_skill') || s.detail?.includes('init_skill');
                                  const inputHas =
                                    s.actionInput?.includes('package_skill') || s.actionInput?.includes('init_skill');
                                  const outputTexts = (execution.outputs[s.id] || [])
                                    .map(o => String(o.content))
                                    .join(' ');
                                  const outputHas =
                                    outputTexts.includes('package_skill') ||
                                    outputTexts.includes('init_skill') ||
                                    outputTexts.includes('Successfully packaged');
                                  return detailHas || inputHas || outputHas;
                                });
                                if (skillStep) {
                                  setSelectedStepId(skillStep.id);
                                  setExecutionMap(prev => ({
                                    ...prev,
                                    [round.viewMsg!.id!]: { ...prev[round.viewMsg!.id!], activeStepId: skillStep.id },
                                  }));
                                }
                              }
                            }}
                            onSkillDownload={async skillName => {
                              try {
                                const base = process.env.API_BASE_URL || '';
                                const res = await fetch(
                                  `${base}/api/v1/agent/skills/download?skill_name=${encodeURIComponent(skillName)}`,
                                );
                                if (!res.ok) throw new Error('Download failed');
                                const blob = await res.blob();
                                const url = URL.createObjectURL(blob);
                                const a = document.createElement('a');
                                a.href = url;
                                a.download = `${skillName}.zip`;
                                document.body.appendChild(a);
                                a.click();
                                document.body.removeChild(a);
                                URL.revokeObjectURL(url);
                              } catch {
                                // Download failed silently
                              }
                            }}
                          />
                          {round.viewMsg?.confirmation && round.viewMsg.id && (
                            <SpecConfirmationCard
                              confirmation={round.viewMsg.confirmation}
                              onDecision={(action, spec, feedback) =>
                                handleSpecDecision(
                                  round.viewMsg!.id!,
                                  round.viewMsg!.confirmation!,
                                  action,
                                  spec,
                                  feedback,
                                )
                              }
                            />
                          )}
                        </div>
                      );
                    })}
                  </div>

                  {/* Input Area at Bottom for Chat Mode - Hidden in read-only task replay mode */}
                  {!router.query.from_task && (
                    <div className='bg-gradient-to-t from-white via-white/95 to-white/80 dark:from-[#1a1b1e] dark:via-[#1a1b1e]/95 dark:to-[#1a1b1e]/80 p-4 md:p-6'>
                      <div className='max-w-[720px] mx-auto'>
                        {/* Context Tags Area */}
                        <div className='flex flex-wrap gap-2 mb-2'>
                          {selectedDb && (
                            <Tag
                              closable
                              onClose={() => setSelectedDb(null)}
                              className='flex items-center gap-1 bg-blue-50 border-blue-200 text-blue-700 px-3 py-1 rounded-full'
                            >
                              {getDbIcon(selectedDb.type)}{' '}
                              <span className='font-medium ml-1'>{selectedDb.db_name}</span>
                            </Tag>
                          )}
                          {selectedKnowledge && (
                            <Tag
                              closable
                              onClose={() => setSelectedKnowledge(null)}
                              className='flex items-center gap-1 bg-orange-50 border-orange-200 text-orange-700 px-3 py-1 rounded-full'
                            >
                              <BookOutlined /> <span className='font-medium ml-1'>{selectedKnowledge.name}</span>
                            </Tag>
                          )}
                          {selectedConnectors.length > 0 && (
                            <>
                              {selectedConnectors.map(c => (
                                <Tag
                                  key={c.id}
                                  closable
                                  onClose={() => setSelectedConnectors(prev => prev.filter(s => s.id !== c.id))}
                                  className='flex items-center gap-1 bg-violet-50 border-violet-200 text-violet-700 px-3 py-1 rounded-full'
                                >
                                  <ApiOutlined /> <span className='font-medium ml-1'>{c.display_name}</span>
                                </Tag>
                              ))}
                            </>
                          )}
                          {renderUploadedFileTags()}
                        </div>

                        {/* Outer Frame - Floating Effect */}
                        <div className='rounded-2xl w-full relative transition-all duration-300 shadow-[0_12px_32px_rgba(0,0,0,0.1),0_4px_12px_rgba(0,0,0,0.06)] hover:shadow-[0_20px_48px_rgba(0,0,0,0.16),0_8px_24px_rgba(0,0,0,0.08)] dark:shadow-[0_12px_32px_rgba(0,0,0,0.4)] dark:hover:shadow-[0_20px_48px_rgba(0,0,0,0.5)]'>
                          {/* White Inner Box - Clean Glass Card */}
                          <div className='bg-white/95 backdrop-blur-md dark:bg-[#1e1f24]/95 rounded-2xl border border-gray-100 dark:border-[#33353b] shadow-[inset_0_1px_0_rgba(255,255,255,1)] dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] p-3 px-4'>
                            {taskPlan.length > 0 && (
                              <div className='mb-3'>
                                <TaskPlanCard tasks={taskPlan} embedded />
                              </div>
                            )}
                            <Input.TextArea
                              value={query}
                              onChange={e => {
                                const newValue = e.target.value;
                                setQuery(newValue);
                                if (newValue === '/' && !isSkillPanelOpen && !selectedSkill) {
                                  openSkillPicker();
                                }
                              }}
                              onPressEnter={e => {
                                if (!e.shiftKey) {
                                  e.preventDefault();
                                  handleStart();
                                }
                              }}
                              placeholder={
                                t('ask_data_question') ||
                                'Ask a question about your database, upload a CSV, or generate a report...'
                              }
                              autoSize={{ minRows: 2, maxRows: 6 }}
                              className='flex-1 resize-none !border-none !shadow-none !bg-transparent px-0 py-2'
                              style={{ backgroundColor: 'transparent' }}
                            />

                            {/* Toolbar Row */}
                            <div className='flex items-center justify-between mt-1'>
                              <div className='flex flex-wrap items-center gap-3'>
                                {/* Add Button */}
                                <Dropdown
                                  menu={{
                                    items: [
                                      {
                                        key: 'upload',
                                        label: (
                                          <span data-testid='backend-qa-upload-menu-item'>Upload files or .zip</span>
                                        ),
                                        icon: <UploadOutlined />,
                                        onClick: openUploadDialog,
                                      },
                                    ],
                                  }}
                                  trigger={['click']}
                                >
                                  <Button
                                    data-testid='backend-qa-add-context'
                                    type='text'
                                    shape='circle'
                                    size='small'
                                    icon={<PlusOutlined />}
                                    className='flex items-center justify-center text-gray-500 hover:text-violet-600 bg-gradient-to-b from-white to-gray-50 dark:from-[#2a2b2f] dark:to-[#1e1f24] dark:text-gray-300 border border-gray-200/80 dark:border-white/10 shadow-[0_1px_2px_rgba(0,0,0,0.05),inset_0_1px_0_rgba(255,255,255,1)] dark:shadow-[0_1px_2px_rgba(0,0,0,0.2),inset_0_1px_0_rgba(255,255,255,0.05)] hover:-translate-y-[0.5px] hover:shadow-[0_2px_4px_rgba(0,0,0,0.06),inset_0_1px_0_rgba(255,255,255,1)] dark:hover:border-white/20 transition-all flex-shrink-0'
                                  />
                                </Dropdown>
                                <MethodHubToggle
                                  enabled={methodHubEnabled}
                                  available={methodHubAvailable}
                                  loading={runtimeCapabilitiesLoading}
                                  label={t('method_hub_toggle')}
                                  unavailableLabel={t('method_hub_unavailable')}
                                  onChange={setMethodHubEnabled}
                                />
                              </div>

                              <div className='flex items-center gap-2.5'>
                                <Button
                                  data-testid='backend-qa-send'
                                  type='primary'
                                  shape='circle'
                                  icon={<ArrowUpOutlined />}
                                  onClick={() => handleStart()}
                                  disabled={(!query.trim() && !hasUploadedFiles) || loading}
                                  loading={loading}
                                  className={`group/send relative overflow-hidden border-none shadow-lg flex-shrink-0 h-9 w-9 transition-all duration-200 ${
                                    query.trim() || hasUploadedFiles
                                      ? 'bg-gradient-to-br from-[#3b82f6] to-[#2563eb] hover:shadow-blue-300/40 hover:shadow-xl hover:scale-105'
                                      : 'bg-gray-200 text-gray-400'
                                  }`}
                                  style={
                                    query.trim() || hasUploadedFiles
                                      ? { background: 'linear-gradient(135deg, #3b82f6, #2563eb)' }
                                      : undefined
                                  }
                                >
                                  {(query.trim() || hasUploadedFiles) && (
                                    <span
                                      className='absolute inset-0 opacity-0 group-hover/send:opacity-100 transition-opacity duration-300 pointer-events-none'
                                      style={{
                                        background:
                                          'linear-gradient(105deg, transparent 40%, rgba(255,255,255,0.25) 45%, rgba(255,255,255,0.35) 50%, rgba(255,255,255,0.25) 55%, transparent 60%)',
                                        animation: 'glossSweepChat 1.8s ease-in-out infinite',
                                      }}
                                    />
                                  )}
                                </Button>
                              </div>
                              <style
                                dangerouslySetInnerHTML={{
                                  __html: `@keyframes glossSweepChat { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }`,
                                }}
                              />
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
                {/* Panel toggle handle — placed between panels to avoid overflow clipping */}
                <div className='relative z-20 flex-shrink-0'>
                  <Tooltip title={rightPanelCollapsed ? t('expand_panel') : t('collapse_panel')} placement='left'>
                    <button
                      onClick={() => setRightPanelCollapsed(prev => !prev)}
                      className='absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-4 h-8 flex items-center justify-center bg-white dark:bg-[#1a1b1e] border border-gray-200 dark:border-gray-700 rounded-full shadow-sm hover:bg-gray-100 dark:hover:bg-gray-800 hover:w-5 hover:shadow-md transition-all duration-200 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'
                    >
                      {rightPanelCollapsed ? (
                        <LeftOutlined style={{ fontSize: 10 }} />
                      ) : (
                        <RightOutlined style={{ fontSize: 10 }} />
                      )}
                    </button>
                  </Tooltip>
                </div>
                <div
                  className={`${rightPanelCollapsed ? 'w-0 min-w-0 overflow-hidden opacity-0' : 'flex-[3] min-w-0 overflow-hidden'} bg-[#f8f8fb] dark:bg-[#0f1114] flex flex-col transition-all duration-300`}
                >
                  {(() => {
                    const activeViewMsg = messages.find(m => m.id === selectedViewMsgId && m.role === 'view');
                    const rawExecution = activeViewMsg?.id ? executionMap[activeViewMsg.id] : undefined;
                    // Respect user's manual step selection for the right panel
                    const execution =
                      rawExecution && selectedStepId ? { ...rawExecution, activeStepId: selectedStepId } : rawExecution;
                    const {
                      activeStep,
                      outputs,
                      stepThoughts: _stepThoughts,
                    } = convertToManusFormat(execution, undefined, t);
                    const isRunning = execution?.steps.some(s => s.status === 'running') || false;

                    return (
                      <ManusRightPanel
                        activeStep={activeStep}
                        outputs={outputs}
                        databaseType={selectedDb?.db_type}
                        databaseName={selectedDb?.db_name}
                        isRunning={isRunning}
                        onCollapse={() => setRightPanelCollapsed(true)}
                        onRerun={router.query.from_task ? undefined : () => {}}
                        onSchedule={
                          !loading && !!conversationId && !router.query.from_task
                            ? () => setScheduleOpen(true)
                            : undefined
                        }
                        terminalTitle={t('db_gpt_computer')}
                        artifacts={artifacts.filter(a => a.messageId === activeViewMsg?.id)}
                        onArtifactClick={artifact => {
                          if (artifact.type === 'html') {
                            setPreviewArtifact(artifact as Artifact);
                            setRightPanelView('html-preview');
                          } else if (artifact.type === 'code' && artifact.stepId) {
                            setSelectedStepId(artifact.stepId);
                            setRightPanelView('execution');
                            if (activeViewMsg?.id && execution) {
                              setExecutionMap(prev => ({
                                ...prev,
                                [activeViewMsg.id!]: {
                                  ...prev[activeViewMsg.id!],
                                  activeStepId: artifact.stepId!,
                                },
                              }));
                            }
                          } else if (artifact.type === 'file') {
                            if (/\.(png|jpg|jpeg|gif|webp|svg|bmp)$/i.test(artifact.name)) {
                              setPreviewArtifact(artifact as Artifact);
                              setRightPanelView('image-preview');
                              setRightPanelCollapsed(false);
                            }
                          } else if (artifact.type === 'image') {
                            setPreviewArtifact(artifact as Artifact);
                            setRightPanelView('image-preview');
                            setRightPanelCollapsed(false);
                          }
                        }}
                        panelView={rightPanelView}
                        onPanelViewChange={setRightPanelView}
                        previewArtifact={previewArtifact}
                        skillName={createdSkillNames[activeViewMsg?.id || ''] || null}
                        summaryContent={streamingSummary || activeViewMsg?.context || ''}
                        isSummaryStreaming={!_summaryComplete && !!streamingSummary}
                      />
                    );
                  })()}
                </div>
              </div>
            ) : (
              // Welcome Mode: Display Hero Section
              <div className='flex-1 flex flex-col items-center justify-center px-6 py-4 pb-20 overflow-y-auto'>
                <div className='w-full max-w-[860px] flex flex-col items-center animate-fade-in-up'>
                  <h1 className='text-4xl md:text-5xl font-serif text-gray-900 dark:text-gray-100 mb-4 text-center flex items-center gap-4'>
                    <div className='w-12 h-12 rounded-xl bg-white dark:bg-[#1a1b1e] shadow-md flex items-center justify-center flex-shrink-0'>
                      <Image src='/LOGO_SMALL.png' alt='DB-GPT' width={32} height={32} className='object-contain' />
                    </div>
                    {t('home_title')}
                  </h1>

                  {/* Input Box Container - Premium Layered Style */}
                  <div className='w-full relative'>
                    {/* Outer Frame - Floating Effect */}
                    <div className='w-full relative transition-all duration-500 rounded-[28px] shadow-[0_16px_48px_rgba(0,0,0,0.12),0_6px_20px_rgba(0,0,0,0.08)] hover:shadow-[0_24px_64px_rgba(0,0,0,0.2),0_12px_32px_rgba(0,0,0,0.1)] dark:shadow-[0_16px_48px_rgba(0,0,0,0.4)] dark:hover:shadow-[0_24px_64px_rgba(0,0,0,0.5)]'>
                      {/* White Inner Box - Clean Glass Card */}
                      <div className='bg-white/95 backdrop-blur-md dark:bg-[#1e1f24]/95 rounded-[28px] border border-gray-100 dark:border-[#33353b] shadow-[inset_0_1px_0_rgba(255,255,255,1)] dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] p-5 relative z-10'>
                        {/* Uploaded File, Database, Knowledge, Connector Tags */}
                        {(hasUploadedFiles || selectedDb || selectedKnowledge || selectedConnectors.length > 0) && (
                          <div className='flex flex-wrap gap-2 mb-2'>
                            {renderUploadedFileTags()}
                            {selectedDb && (
                              <Tag
                                closable
                                onClose={() => setSelectedDb(null)}
                                className='flex items-center gap-1 bg-blue-50 border-blue-200 text-blue-700 px-3 py-1 rounded-full'
                              >
                                {getDbIcon(selectedDb.type)}{' '}
                                <span className='font-medium ml-1'>{selectedDb.db_name}</span>
                              </Tag>
                            )}
                            {selectedKnowledge && (
                              <Tag
                                closable
                                onClose={() => setSelectedKnowledge(null)}
                                className='flex items-center gap-1 bg-orange-50 border-orange-200 text-orange-700 px-3 py-1 rounded-full'
                              >
                                <BookOutlined /> <span className='font-medium ml-1'>{selectedKnowledge.name}</span>
                              </Tag>
                            )}
                            {selectedConnectors.length > 0 && (
                              <>
                                {selectedConnectors.map(c => (
                                  <Tag
                                    key={c.id}
                                    closable
                                    onClose={() => setSelectedConnectors(prev => prev.filter(s => s.id !== c.id))}
                                    className='flex items-center gap-1 bg-violet-50 border-violet-200 text-violet-700 px-3 py-1 rounded-full'
                                  >
                                    <ApiOutlined /> <span className='font-medium ml-1'>{c.display_name}</span>
                                  </Tag>
                                ))}
                              </>
                            )}
                          </div>
                        )}

                        <Input.TextArea
                          value={query}
                          onChange={e => {
                            const newValue = e.target.value;
                            setQuery(newValue);
                            if (newValue === '/' && !isSkillPanelOpen && !selectedSkill) {
                              setIsSkillPanelOpen(true);
                            }
                          }}
                          onPressEnter={e => {
                            if (!e.shiftKey) {
                              e.preventDefault();
                              handleStart();
                            }
                          }}
                          placeholder={
                            t('ask_data_question') ||
                            'Ask a question about your database, upload a CSV, or generate a report...'
                          }
                          autoSize={{ minRows: 3, maxRows: 8 }}
                          className='text-lg resize-none !border-none !shadow-none !bg-transparent px-2 py-2 mb-2'
                          style={{ backgroundColor: 'transparent' }}
                        />

                        {/* Input Toolbar */}
                        <div className='flex items-center justify-between px-1 mt-1'>
                          <div className='flex flex-wrap items-center gap-4'>
                            {/* Add Button with Dropdown Menu */}
                            <Dropdown
                              menu={{
                                items: [
                                  {
                                    key: 'upload',
                                    label: <span data-testid='backend-qa-upload-menu-item'>Upload files or .zip</span>,
                                    icon: <PaperClipOutlined />,
                                    onClick: openUploadDialog,
                                  },
                                ],
                              }}
                              trigger={['click']}
                            >
                              <Button
                                data-testid='backend-qa-add-context'
                                type='text'
                                shape='circle'
                                size='small'
                                icon={<PlusOutlined />}
                                className='flex items-center justify-center text-gray-500 hover:text-violet-600 bg-gradient-to-b from-white to-gray-50 dark:from-[#2a2b2f] dark:to-[#1e1f24] dark:text-gray-300 border border-gray-200/80 dark:border-white/10 shadow-[0_1px_2px_rgba(0,0,0,0.05),inset_0_1px_0_rgba(255,255,255,1)] dark:shadow-[0_1px_2px_rgba(0,0,0,0.2),inset_0_1px_0_rgba(255,255,255,0.05)] hover:-translate-y-[0.5px] hover:shadow-[0_2px_4px_rgba(0,0,0,0.06),inset_0_1px_0_rgba(255,255,255,1)] dark:hover:border-white/20 transition-all flex-shrink-0'
                              />
                            </Dropdown>

                            {/* Model Selector with premium styling */}
                            <div className='model-selector-premium'>
                              <ModelSelector onChange={val => setModel(val)} />
                            </div>
                            <MethodHubToggle
                              enabled={methodHubEnabled}
                              available={methodHubAvailable}
                              loading={runtimeCapabilitiesLoading}
                              label={t('method_hub_toggle')}
                              unavailableLabel={t('method_hub_unavailable')}
                              onChange={setMethodHubEnabled}
                            />
                            <style
                              dangerouslySetInnerHTML={{
                                __html: `
                                  .model-selector-premium .ant-select { border-radius: 8px !important; border: none !important; }
                                  .model-selector-premium .ant-select-selector { background: linear-gradient(180deg, #ffffff 0%, #f9fafb 100%) !important; border: 1px solid rgba(0,0,0,0.12) !important; box-shadow: 0 1px 2px rgba(0,0,0,0.05), inset 0 1px 0 rgba(255,255,255,1) !important; border-radius: 8px !important; transition: all 0.2s ease !important; padding: 0 8px !important; }
                                  .dark .model-selector-premium .ant-select-selector { background: linear-gradient(180deg, #2a2b2f 0%, #1e1f24 100%) !important; border: 1px solid rgba(255,255,255,0.1) !important; box-shadow: 0 1px 2px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05) !important; }
                                  .model-selector-premium .ant-select:hover .ant-select-selector { border-color: rgba(0,0,0,0.2) !important; box-shadow: 0 2px 4px rgba(0,0,0,0.06), inset 0 1px 0 rgba(255,255,255,1) !important; transform: translateY(-0.5px); }
                                  .dark .model-selector-premium .ant-select:hover .ant-select-selector { border-color: rgba(255,255,255,0.15) !important; }
                                  .model-selector-premium .ant-select-focused .ant-select-selector { border-color: #a78bfa !important; box-shadow: 0 0 0 2px rgba(167,139,250,0.15), inset 0 1px 0 rgba(255,255,255,1) !important; }
                                  .dark .model-selector-premium .ant-select-focused .ant-select-selector { box-shadow: 0 0 0 2px rgba(167,139,250,0.2), inset 0 1px 0 rgba(255,255,255,0.05) !important; }
                                  
                                  /* Global Dropdown Item Styles for Model Selectors */
                                  .ant-select-dropdown .ant-select-item-option-selected { background-color: #f1f5f9 !important; color: #0f172a !important; font-weight: 500 !important; }
                                  .ant-select-dropdown .ant-select-item-option-active:not(.ant-select-item-option-selected) { background-color: #f8fafc !important; }
                                  .dark .ant-select-dropdown .ant-select-item-option-selected { background-color: rgba(255,255,255,0.08) !important; color: #e2e8f0 !important; }
                                  .dark .ant-select-dropdown .ant-select-item-option-active:not(.ant-select-item-option-selected) { background-color: rgba(255,255,255,0.04) !important; }
                                `,
                              }}
                            />
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Recommended Examples */}
                  <div className='mt-10 w-full'>
                    <div className='flex items-center justify-center gap-2 mb-4'>
                      <div className='h-px flex-1 bg-gradient-to-r from-transparent to-gray-200 dark:to-gray-700' />
                      <span className='text-xs font-medium text-gray-400 dark:text-gray-500 tracking-wider uppercase'>
                        {t('recommend_examples')}
                      </span>
                      <div className='h-px flex-1 bg-gradient-to-l from-transparent to-gray-200 dark:to-gray-700' />
                    </div>
                    <div className='grid grid-cols-1 sm:grid-cols-2 gap-3'>
                      {EXAMPLE_CARDS.map(example => (
                        <div
                          key={example.id}
                          onClick={() => handleExampleClick(example)}
                          className={`group relative bg-gradient-to-br ${example.color} border ${example.borderColor} rounded-2xl p-4 cursor-pointer hover:shadow-lg hover:-translate-y-0.5 transition-all duration-300`}
                        >
                          <div className='flex items-start gap-3'>
                            <div
                              className={`w-10 h-10 ${example.iconBg} rounded-xl flex items-center justify-center text-xl flex-shrink-0`}
                            >
                              {example.icon}
                            </div>
                            <div className='flex-1 min-w-0'>
                              <h3 className='text-sm font-semibold text-gray-800 dark:text-gray-200 mb-1'>
                                {(() => {
                                  const key = `example_${example.id}_title`;
                                  const val = t(key) as string;
                                  return val && val !== key ? val : example.title;
                                })()}
                              </h3>
                              <p className='text-xs text-gray-500 dark:text-gray-400 line-clamp-2'>
                                {(() => {
                                  const key = `example_${example.id}_desc`;
                                  const val = t(key) as string;
                                  return val && val !== key ? val : example.description;
                                })()}
                              </p>
                            </div>
                          </div>
                          <div className='absolute top-4 right-4 opacity-0 group-hover:opacity-100 transition-opacity'>
                            <RightOutlined className='text-xs text-gray-400' />
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Database Selection Modal */}
          <Modal
            title={
              <div className='flex items-center gap-2'>
                <DatabaseOutlined />
                Select Data Source
              </div>
            }
            open={isDbModalOpen}
            onCancel={() => setIsDbModalOpen(false)}
            footer={null}
            width={500}
          >
            <List
              itemLayout='horizontal'
              dataSource={dataSources || []}
              renderItem={(item: DataSource) => (
                <List.Item
                  className={`cursor-pointer hover:bg-gray-50 rounded-lg px-2 transition-colors ${selectedDb?.id === item.id ? 'bg-blue-50' : ''}`}
                  onClick={() => {
                    setSelectedDb(item);
                    setIsDbModalOpen(false);
                  }}
                  actions={[selectedDb?.id === item.id && <CheckCircleFilled className='text-blue-500' />]}
                >
                  <List.Item.Meta
                    avatar={<div className='mt-1 bg-gray-100 p-2 rounded-lg'>{getDbIcon(item.type)}</div>}
                    title={item.db_name}
                    description={<span className='text-xs text-gray-400'>{item.type}</span>}
                  />
                </List.Item>
              )}
              locale={{ emptyText: 'No data sources found' }}
            />
            <div className='mt-4 pt-4 border-t border-gray-100 text-center'>
              <Button type='dashed' block icon={<PlusOutlined />} onClick={() => router.push('/construct/database')}>
                Add New Data Source
              </Button>
            </div>
          </Modal>

          {/* Knowledge Base Selection Modal */}
          <Modal
            title={
              <div className='flex items-center gap-2'>
                <BookOutlined />
                Select Knowledge Base
              </div>
            }
            open={isKnowledgeModalOpen}
            onCancel={() => setIsKnowledgeModalOpen(false)}
            footer={null}
            width={500}
          >
            <List
              itemLayout='horizontal'
              dataSource={knowledgeSpaces || []}
              renderItem={(item: KnowledgeSpace) => (
                <List.Item
                  className={`cursor-pointer hover:bg-gray-50 rounded-lg px-2 transition-colors ${selectedKnowledge?.id === item.id ? 'bg-orange-50' : ''}`}
                  onClick={() => {
                    setSelectedKnowledge(item);
                    setIsKnowledgeModalOpen(false);
                  }}
                  actions={[selectedKnowledge?.id === item.id && <CheckCircleFilled className='text-orange-500' />]}
                >
                  <List.Item.Meta
                    avatar={
                      <div className='mt-1 bg-gray-100 p-2 rounded-lg'>
                        <ReadOutlined className='text-orange-500' />
                      </div>
                    }
                    title={item.name}
                    description={<span className='text-xs text-gray-400'>{item.vector_type}</span>}
                  />
                </List.Item>
              )}
              locale={{ emptyText: 'No knowledge bases found' }}
            />
            <div className='mt-4 pt-4 border-t border-gray-100 text-center'>
              <Button type='dashed' block icon={<PlusOutlined />} onClick={() => router.push('/construct/knowledge')}>
                Add New Knowledge Base
              </Button>
            </div>
          </Modal>
          <SaveAsScheduledTaskDrawer
            open={isScheduleOpen}
            onClose={() => setScheduleOpen(false)}
            snapshot={buildSnapshot()}
          />
        </div>
      </>
    </ConfigProvider>
  );
};

export default Playground;
