// Sets the Android system locale without root: run as the shell user via app_process,
// which holds CHANGE_CONFIGURATION. Play Store emulator images cannot `setprop` it.
//   CLASSPATH=/data/local/tmp/setlocale.dex app_process / SetLocale cs-CZ
import android.content.res.Configuration;
import android.os.LocaleList;
import java.lang.reflect.Method;

public class SetLocale {
    public static void main(String[] args) throws Exception {
        String tag = args.length > 0 ? args[0] : "cs-CZ";
        Object am = Class.forName("android.app.ActivityManager").getMethod("getService").invoke(null);
        Configuration config = (Configuration) am.getClass().getMethod("getConfiguration").invoke(am);
        config.setLocales(LocaleList.forLanguageTags(tag));
        try {
            Configuration.class.getField("userSetLocale").setBoolean(config, true);
        } catch (Throwable t) {
            System.out.println("userSetLocale not set: " + t);
        }
        try {
            Method m = am.getClass().getMethod("updatePersistentConfigurationWithAttribution",
                    Configuration.class, String.class, String.class);
            m.invoke(am, config, "com.android.shell", null);
        } catch (NoSuchMethodException e) {
            am.getClass().getMethod("updatePersistentConfiguration", Configuration.class).invoke(am, config);
        }
        System.out.println("locale set to " + tag);
    }
}
