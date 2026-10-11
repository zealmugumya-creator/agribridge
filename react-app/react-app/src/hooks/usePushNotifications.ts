/**
 * Copyright (C) 2026 Mugumya Zeal. All rights reserved.
 */

import { useEffect } from 'react';
import { Capacitor } from '@capacitor/core';
import { PushNotifications } from '@capacitor/push-notifications';
import { supabase } from '../lib/supabase';

export function usePushNotifications(userId: string | null) {
  useEffect(() => {
    if (!userId || !Capacitor.isNativePlatform()) return;

    const register = async () => {
      let permResult = await PushNotifications.checkPermissions();
      if (permResult.receive === 'prompt') {
        permResult = await PushNotifications.requestPermissions();
      }
      if (permResult.receive !== 'granted') return;

      await PushNotifications.register();
    };

    // Save FCM token to Supabase so backend can send targeted pushes
    const tokenHandler = PushNotifications.addListener('registration', async (token) => {
      await supabase
        .from('push_tokens')
        .upsert({ user_id: userId, token: token.value, platform: 'android' });
    });

    const errorHandler = PushNotifications.addListener('registrationError', (err) => {
      console.error('Push registration error:', err);
    });

    register();

    return () => {
      tokenHandler.then(h => h.remove());
      errorHandler.then(h => h.remove());
    };
  }, [userId]);
}
