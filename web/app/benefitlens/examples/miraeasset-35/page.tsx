import BenefitLensDashboard from '../../benefitlens-dashboard';
import { APPLICATION_EXAMPLE_ID } from '@/lib/application-package';

export default function ApplicationExamplePage() {
  return <BenefitLensDashboard view="draft" exampleId={APPLICATION_EXAMPLE_ID} />;
}
