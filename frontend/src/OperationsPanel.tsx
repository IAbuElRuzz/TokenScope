import type {ApiRequest} from './api';
import {BudgetsPanel} from './BudgetsPanel';
import {CollectorHealthPanel} from './CollectorHealthPanel';
export function OperationsPanel({request}:{request:ApiRequest}){return <section id="operations" className="operations-panel" aria-label="Operational monitoring"><CollectorHealthPanel request={request}/><BudgetsPanel request={request}/></section>;}
