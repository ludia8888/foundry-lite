import type { WidgetKind } from "../../lib/app-model";
import { ActionFormWidget, ButtonGroupWidget } from "./ActionWidgets";
import {
  AipChatbotWidget,
  DividerWidget,
  MarkdownWidget,
  SectionHeaderWidget,
} from "./ContentWidgets";
import {
  LinksWidget,
  ObjectDetailWidget,
  ObjectLinksWidget,
  ObjectListWidget,
  ObjectSetTitleWidget,
  ObjectTableWidget,
} from "./DisplayWidgets";
import {
  FilterListWidget,
  ObjectDropdownWidget,
  SearchBarWidget,
  StringSelectorWidget,
} from "./InputWidgets";
import {
  BarChartWidget,
  MetricCardWidget,
  PieChartWidget,
  TimelineWidget,
} from "./VizWidgets";
import {
  CalendarWidget,
  KanbanWidget,
  PivotTableWidget,
  StatusTrackerWidget,
} from "./OperationalWidgets";
import type { WidgetRuntimeProps } from "./widget-kit";

type WidgetComponent = (props: WidgetRuntimeProps) => React.ReactNode;

export const WIDGET_RENDERERS: Record<WidgetKind, WidgetComponent> = {
  objectTable: ObjectTableWidget,
  objectList: ObjectListWidget,
  objectDetail: ObjectDetailWidget,
  objectSetTitle: ObjectSetTitleWidget,
  links: LinksWidget,
  objectLinks: ObjectLinksWidget,
  metricCard: MetricCardWidget,
  barChart: BarChartWidget,
  pieChart: PieChartWidget,
  timeline: TimelineWidget,
  kanban: KanbanWidget,
  calendar: CalendarWidget,
  statusTracker: StatusTrackerWidget,
  pivotTable: PivotTableWidget,
  filterList: FilterListWidget,
  objectDropdown: ObjectDropdownWidget,
  searchBar: SearchBarWidget,
  stringSelector: StringSelectorWidget,
  buttonGroup: ButtonGroupWidget,
  actionForm: ActionFormWidget,
  markdown: MarkdownWidget,
  sectionHeader: SectionHeaderWidget,
  divider: DividerWidget,
  aipChatbot: AipChatbotWidget,
};

export function WidgetRenderer(props: WidgetRuntimeProps) {
  const Component = WIDGET_RENDERERS[props.widget.kind];
  if (!Component) return null;
  return <Component {...props} />;
}
