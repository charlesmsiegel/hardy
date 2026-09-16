// The reading half of `SessionProvider`. Thrown rather than returning
// `null` on a miss, because a component that forgot the provider would
// otherwise fail on whatever field it first touches, far from the mistake.

import {useContext} from 'react';
import {SessionContext} from './SessionProvider.jsx';

export default function useSession() {
  const value = useContext(SessionContext);
  if (value === null) throw new Error('useSession() called outside a SessionProvider');
  return value;
}
